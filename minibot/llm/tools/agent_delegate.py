from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from llm_async.models import Tool

from minibot.app.agent_policies import filter_tools_for_agent
from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_runtime import AgentRuntime
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.post_answer_gate import PostAnswerGate
from minibot.app.runtime_limits import build_runtime_limits
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.core.agents import AgentSpec
from minibot.llm.tools.arg_utils import optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.schema_utils import empty_object_schema, strict_object, string_field
from minibot.shared.assistant_response import (
    assistant_response_schema,
    coerce_should_answer,
    payload_to_object,
    validate_attachments,
)


@dataclass(frozen=True)
class _DelegationOutcome:
    text: str
    payload: Any
    should_answer_to_user: bool | None
    valid: bool
    error_code: str | None
    attachments: list[dict[str, Any]]


class AgentDelegateTool:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        llm_factory: LLMClientFactory,
        tools: Sequence[ToolBinding],
        default_timeout_seconds: int,
        delegated_tool_call_policy: Literal["auto", "always", "never"] = "auto",
        environment_prompt_fragment: str = "",
        post_answer_gate_enabled: bool = True,
        post_answer_gate_scope: Literal["main_only", "all_agents"] = "main_only",
        post_answer_gate_max_retries: int = 1,
    ) -> None:
        self._registry = registry
        self._llm_factory = llm_factory
        self._tools = list(tools)
        self._default_timeout_seconds = default_timeout_seconds
        self._delegated_tool_call_policy = delegated_tool_call_policy
        self._environment_prompt_fragment = environment_prompt_fragment.strip()
        self._post_answer_gate_enabled = post_answer_gate_enabled
        self._post_answer_gate_scope = post_answer_gate_scope
        self._post_answer_gate_max_retries = post_answer_gate_max_retries
        self._logger = logging.getLogger("minibot.agent_delegate")

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._agent_delegate_schema(), handler=self._agent_delegate),
            ToolBinding(tool=self._list_agents_schema(), handler=self._list_agents),
            ToolBinding(tool=self._invoke_agent_schema(), handler=self._invoke_agent),
        ]

    def _agent_delegate_schema(self) -> Tool:
        return Tool(
            name="agent_delegate",
            description="Agent delegation operations. Use action=list|invoke.",
            parameters=strict_object(
                properties={
                    "action": {
                        "type": "string",
                        "enum": ["list", "invoke"],
                        "description": "Delegation operation to perform.",
                    },
                    "agent_name": string_field("Exact name returned by agent_delegate action=list."),
                    "task": string_field("Concrete delegated task for the specialist."),
                    "context": string_field("Optional supporting context for the specialist."),
                },
                required=["action"],
            ),
        )

    def _list_agents_schema(self) -> Tool:
        return Tool(
            name="list_agents",
            description=(
                "List available specialist agents that can be invoked by name. "
                "Use this before invoking a specialist when uncertain about agent names."
            ),
            parameters=empty_object_schema(),
        )

    def _invoke_agent_schema(self) -> Tool:
        return Tool(
            name="invoke_agent",
            description=(
                "Invoke a specialist agent to handle a delegated task. "
                "Wait for its result and then continue with your own final answer."
            ),
            parameters=strict_object(
                properties={
                    "agent_name": string_field("Exact name returned by list_agents."),
                    "task": string_field("Concrete delegated task for the specialist."),
                    "context": string_field("Optional supporting context for the specialist."),
                },
                required=["agent_name", "task"],
            ),
        )

    async def _list_agents(self, _: dict[str, object], __: ToolContext) -> dict[str, object]:
        names = sorted(spec.name for spec in self._registry.all())
        return {"agents": names, "count": len(names)}

    async def _invoke_agent(self, payload: dict[str, object], context: ToolContext) -> dict[str, object]:
        agent_name = require_non_empty_str(payload, "agent_name")
        task = require_non_empty_str(payload, "task")
        details = optional_str(payload.get("context"), error_message="context must be a string")
        spec = self._registry.get(agent_name)
        if spec is None:
            return {
                "ok": False,
                "agent": agent_name,
                "error_code": "agent_not_found",
                "error": f"agent '{agent_name}' is not available",
            }

        self._logger.debug(
            "delegated agent invocation started",
            extra={
                "agent": agent_name,
                "task_preview": task[:240],
                "has_context": bool(details),
            },
        )

        llm_client = self._llm_factory.create_for_agent(spec)
        scoped_tools = self._scoped_tools(spec)
        runtime = AgentRuntime(
            llm_client=llm_client,
            tools=scoped_tools,
            limits=build_runtime_limits(
                llm_client=llm_client,
                timeout_seconds=self._default_timeout_seconds,
                min_timeout_seconds=30,
            ),
            allowed_append_message_tools=["self_insert_artifact", "artifact_insert"],
            allow_system_inserts=False,
            managed_files_root=None,
        )
        gate_enabled = self._should_apply_gate()
        gate: PostAnswerGate | None = None
        if gate_enabled and scoped_tools:
            gate = PostAnswerGate(llm_client=llm_client, tools=scoped_tools)

        tool_required = self._delegated_tool_call_required(spec)
        attempts = 1
        state = self._build_state(spec=spec, task=task, details=details)
        total_tokens = 0
        gate_tokens = 0
        try:
            generation = await runtime.run(
                state=state,
                tool_context=context,
                response_schema=_agent_response_schema(),
                prompt_cache_key=None,
            )
            total_tokens += int(generation.total_tokens or 0)
            tool_messages_count = self._count_tool_messages(generation.state)
            outcome = _extract_outcome(generation.payload)

            should_retry = False
            retry_reason = ""

            if gate is not None and tool_messages_count == 0:
                gate_decision = await gate.evaluate(
                    session_id=f"delegate:{spec.name}",
                    history=[],
                    user_text=task,
                    assistant_response=outcome.text,
                    state=generation.state,
                    prompt_cache_key=None,
                )
                gate_tokens += gate_decision.tokens_used
                total_tokens += gate_decision.tokens_used

                self._logger.debug(
                    "delegated agent post-answer gate decision",
                    extra={
                        "agent": spec.name,
                        "action": gate_decision.action,
                        "reason_code": gate_decision.reason_code,
                        "requires_tools": gate_decision.requires_tools,
                        "suggested_tool": gate_decision.suggested_tool,
                        "gate_tokens": gate_decision.tokens_used,
                    },
                )

                if gate_decision.action == "continue_with_tools":
                    should_retry = True
                    retry_reason = f"gate:{gate_decision.reason_code}"
            elif tool_required and tool_messages_count == 0:
                should_retry = True
                retry_reason = f"policy:{self._delegated_tool_call_policy}"

            if should_retry and tool_messages_count == 0:
                retry_count = 0
                max_retries = self._post_answer_gate_max_retries if gate is not None else 1

                while retry_count < max_retries and tool_messages_count == 0:
                    attempts += 1
                    retry_count += 1
                    self._logger.debug(
                        "delegated agent retry requested",
                        extra={
                            "agent": spec.name,
                            "retry_reason": retry_reason,
                            "retry_count": retry_count,
                            "max_retries": max_retries,
                        },
                    )
                    retry_state = self._build_state(
                        spec=spec,
                        task=task,
                        details=details,
                        system_prompt_override=(
                            f"{spec.system_prompt}\n\n"
                            "Tool policy reminder: this delegated task requires at least one tool call before "
                            "your final answer. Execute the necessary tool now, then return the result."
                        ),
                    )
                    generation = await runtime.run(
                        state=retry_state,
                        tool_context=context,
                        response_schema=_agent_response_schema(),
                        prompt_cache_key=None,
                    )
                    total_tokens += int(generation.total_tokens or 0)
                    tool_messages_count = self._count_tool_messages(generation.state)
                    outcome = _extract_outcome(generation.payload)

                    if tool_messages_count > 0:
                        self._logger.debug(
                            "delegated agent retry succeeded",
                            extra={
                                "agent": spec.name,
                                "tool_messages": tool_messages_count,
                                "retry_count": retry_count,
                            },
                        )
                        break

                if tool_messages_count == 0:
                    self._logger.warning(
                        "delegated agent failed tool-use requirement after retries",
                        extra={
                            "agent": spec.name,
                            "retry_reason": retry_reason,
                            "attempts": attempts,
                        },
                    )
                    return {
                        "ok": False,
                        "agent": spec.name,
                        "result": "Delegated agent did not execute required tools.",
                        "result_status": "invalid_result",
                        "should_answer_to_user": False,
                        "tool_count": len(scoped_tools),
                        "tool_messages_count": tool_messages_count,
                        "delegation_attempts": attempts,
                        "total_tokens": total_tokens,
                        "gate_tokens": gate_tokens,
                        "error_code": "delegated_no_tool_calls",
                        "error": "delegated agent returned without required tool calls",
                    }
            result_status = _delegation_result_status(outcome)
            ok = result_status == "success"
            if outcome.attachments:
                self._logger.debug(
                    "delegation returned attachments",
                    extra={
                        "agent": spec.name,
                        "attachment_count": len(outcome.attachments),
                        "attachment_types": [a.get("type") for a in outcome.attachments],
                    },
                )
            log_extra = {
                "agent": spec.name,
                "tool_count": len(scoped_tools),
                "total_tokens": total_tokens,
                "tool_messages_count": tool_messages_count,
                "delegation_attempts": attempts,
                "result_status": result_status,
                "result_preview": outcome.text[:240],
            }
            if gate_tokens > 0:
                log_extra["gate_tokens"] = gate_tokens
            self._logger.debug("delegated agent invocation completed", extra=log_extra)
            response: dict[str, Any] = {
                "ok": ok,
                "agent": spec.name,
                "result": outcome.text,
                "payload": outcome.payload,
                "result_status": result_status,
                "should_answer_to_user": outcome.should_answer_to_user,
                "tool_count": len(scoped_tools),
                "tool_messages_count": tool_messages_count,
                "delegation_attempts": attempts,
                "total_tokens": total_tokens,
                "attachments": outcome.attachments,
            }
            if gate_tokens > 0:
                response["gate_tokens"] = gate_tokens
            if outcome.error_code is not None:
                response["error_code"] = outcome.error_code
                response["error"] = f"delegated agent returned {outcome.error_code}"
            return response
        except Exception as exc:
            self._logger.exception(
                "delegated agent invocation failed",
                extra={"agent": spec.name},
            )
            return {
                "ok": False,
                "agent": spec.name,
                "error_code": "delegated_agent_failed",
                "error": str(exc),
                "delegation_attempts": attempts,
            }

    async def _agent_delegate(self, payload: dict[str, object], context: ToolContext) -> dict[str, object]:
        action = optional_str(payload.get("action"), error_message="action must be a string")
        if action == "list":
            return await self._list_agents(payload, context)
        if action == "invoke":
            return await self._invoke_agent(payload, context)
        raise ValueError("action must be one of: list, invoke")

    def _scoped_tools(self, spec: AgentSpec) -> list[ToolBinding]:
        scoped = filter_tools_for_agent(self._tools, spec)
        return [binding for binding in scoped if binding.tool.name != "invoke_agent"]

    def _build_state(
        self,
        *,
        spec: AgentSpec,
        task: str,
        details: str | None,
        system_prompt_override: str | None = None,
    ) -> AgentState:
        user_text = task
        if details:
            user_text = f"Task:\n{task}\n\nContext:\n{details}"
        system_prompt = system_prompt_override or spec.system_prompt
        if self._environment_prompt_fragment:
            system_prompt = f"{system_prompt}\n\n{self._environment_prompt_fragment}"
        return AgentState(
            messages=[
                AgentMessage(
                    role="system",
                    content=[MessagePart(type="text", text=system_prompt)],
                ),
                AgentMessage(role="user", content=[MessagePart(type="text", text=user_text)]),
            ]
        )

    def _delegated_tool_call_required(self, spec: AgentSpec) -> bool:
        if self._delegated_tool_call_policy == "never":
            return False
        if self._delegated_tool_call_policy == "always":
            return True
        scoped_tools = self._scoped_tools(spec)
        return len(scoped_tools) > 0

    def _should_apply_gate(self) -> bool:
        if not self._post_answer_gate_enabled:
            return False
        return self._post_answer_gate_scope == "all_agents"

    @staticmethod
    def _count_tool_messages(state: AgentState) -> int:
        return sum(1 for message in state.messages if message.role == "tool")


def _agent_response_schema() -> dict[str, Any]:
    return assistant_response_schema(kinds=["text", "html", "markdown", "json"], include_attachments=True)


def _validate_attachments(raw_attachments: Any) -> list[dict[str, Any]]:
    return validate_attachments(raw_attachments)


def _extract_outcome(payload: Any) -> _DelegationOutcome:
    payload_obj = payload_to_object(payload)
    if payload_obj is not None:
        answer = payload_obj.get("answer")
        should_flag = coerce_should_answer(payload_obj.get("should_answer_to_user"))
        attachments = validate_attachments(payload_obj.get("attachments"))
        if should_flag is None:
            return _DelegationOutcome(
                text=str(payload_obj),
                payload=payload_obj,
                should_answer_to_user=None,
                valid=False,
                error_code="missing_should_answer_to_user",
                attachments=attachments,
            )
        if isinstance(answer, dict):
            content = answer.get("content")
            if isinstance(content, str) and content.strip():
                return _DelegationOutcome(
                    text=content,
                    payload=payload_obj,
                    should_answer_to_user=should_flag,
                    valid=True,
                    error_code=None,
                    attachments=attachments,
                )
            return _DelegationOutcome(
                text=str(payload_obj),
                payload=payload_obj,
                should_answer_to_user=should_flag,
                valid=False,
                error_code="missing_answer_content",
                attachments=attachments,
            )
        return _DelegationOutcome(
            text=str(payload_obj),
            payload=payload_obj,
            should_answer_to_user=should_flag,
            valid=False,
            error_code="missing_answer_object",
            attachments=attachments,
        )
    return _DelegationOutcome(
        text=str(payload),
        payload=payload,
        should_answer_to_user=None,
        valid=False,
        error_code="unstructured_payload",
        attachments=[],
    )


def _delegation_result_status(outcome: _DelegationOutcome) -> str:
    if not outcome.valid:
        return "invalid_result"
    if outcome.should_answer_to_user is False:
        return "not_user_answerable"
    return "success"
