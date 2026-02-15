from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from llm_async.models import Tool

from minibot.app.agent_policies import filter_tools_for_agent
from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_runtime import AgentRuntime
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart, RuntimeLimits
from minibot.core.agents import AgentSpec
from minibot.llm.tools.arg_utils import optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.schema_utils import empty_object_schema, strict_object, string_field
from minibot.shared.parse_utils import parse_json_maybe_python_object


@dataclass(frozen=True)
class _DelegationOutcome:
    text: str
    payload: Any
    should_answer_to_user: bool | None
    valid: bool
    error_code: str | None


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
    ) -> None:
        self._registry = registry
        self._llm_factory = llm_factory
        self._tools = list(tools)
        self._default_timeout_seconds = default_timeout_seconds
        self._delegated_tool_call_policy = delegated_tool_call_policy
        self._environment_prompt_fragment = environment_prompt_fragment.strip()
        self._logger = logging.getLogger("minibot.agent_delegate")

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._list_agents_schema(), handler=self._list_agents),
            ToolBinding(tool=self._invoke_agent_schema(), handler=self._invoke_agent),
        ]

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
        max_tool_iterations = _max_tool_iterations(llm_client)
        runtime = AgentRuntime(
            llm_client=llm_client,
            tools=scoped_tools,
            limits=RuntimeLimits(
                max_steps=max(1, max_tool_iterations),
                max_tool_calls=max(12, max_tool_iterations * 2),
                timeout_seconds=max(30, int(self._default_timeout_seconds)),
            ),
            allowed_append_message_tools=["self_insert_artifact"],
            allow_system_inserts=False,
            managed_files_root=None,
        )
        tool_required = self._delegated_tool_call_required(spec)
        attempts = 1
        state = self._build_state(spec=spec, task=task, details=details)
        total_tokens = 0
        try:
            generation = await runtime.run(
                state=state,
                tool_context=context,
                response_schema=_agent_response_schema(),
                prompt_cache_key=None,
            )
            total_tokens += int(generation.total_tokens or 0)
            tool_messages_count = self._count_tool_messages(generation.state)
            if tool_required and tool_messages_count == 0:
                attempts += 1
                self._logger.warning(
                    "delegated agent returned without tool calls; retrying once",
                    extra={
                        "agent": spec.name,
                        "policy": self._delegated_tool_call_policy,
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
                if tool_messages_count == 0:
                    self._logger.warning(
                        "delegated agent failed tool-use requirement",
                        extra={
                            "agent": spec.name,
                            "policy": self._delegated_tool_call_policy,
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
                        "error_code": "delegated_no_tool_calls",
                        "error": "delegated agent returned without required tool calls",
                    }
            outcome = _extract_outcome(generation.payload)
            result_status = _delegation_result_status(outcome)
            ok = result_status == "success"
            self._logger.debug(
                "delegated agent invocation completed",
                extra={
                    "agent": spec.name,
                    "tool_count": len(scoped_tools),
                    "total_tokens": total_tokens,
                    "tool_messages_count": tool_messages_count,
                    "delegation_attempts": attempts,
                    "result_status": result_status,
                    "result_preview": outcome.text[:240],
                },
            )
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
            }
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

    @staticmethod
    def _count_tool_messages(state: AgentState) -> int:
        return sum(1 for message in state.messages if message.role == "tool")


def _max_tool_iterations(llm_client: Any) -> int:
    max_tool_iterations_getter = getattr(llm_client, "max_tool_iterations", None)
    if callable(max_tool_iterations_getter):
        maybe_iterations = max_tool_iterations_getter()
        if isinstance(maybe_iterations, int) and maybe_iterations > 0:
            return maybe_iterations
    return 8


def _agent_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "answer": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["text", "html", "markdown", "json"]},
                    "content": {"type": "string"},
                },
                "required": ["kind", "content"],
                "additionalProperties": False,
            },
            "should_answer_to_user": {"type": "boolean"},
        },
        "required": ["answer", "should_answer_to_user"],
        "additionalProperties": False,
    }


def _extract_outcome(payload: Any) -> _DelegationOutcome:
    if isinstance(payload, str):
        parsed = parse_json_maybe_python_object(payload)
        if parsed is not None and parsed is not payload:
            return _extract_outcome(parsed)
        return _DelegationOutcome(
            text=payload,
            payload=payload,
            should_answer_to_user=None,
            valid=False,
            error_code="unstructured_payload",
        )
    if isinstance(payload, dict):
        answer = payload.get("answer")
        should = payload.get("should_answer_to_user")
        should_flag = should if isinstance(should, bool) else None
        if should_flag is None:
            return _DelegationOutcome(
                text=str(payload),
                payload=payload,
                should_answer_to_user=None,
                valid=False,
                error_code="missing_should_answer_to_user",
            )
        if isinstance(answer, dict):
            content = answer.get("content")
            if isinstance(content, str) and content.strip():
                return _DelegationOutcome(
                    text=content,
                    payload=payload,
                    should_answer_to_user=should_flag,
                    valid=True,
                    error_code=None,
                )
            return _DelegationOutcome(
                text=str(payload),
                payload=payload,
                should_answer_to_user=should_flag,
                valid=False,
                error_code="missing_answer_content",
            )
        return _DelegationOutcome(
            text=str(payload),
            payload=payload,
            should_answer_to_user=should_flag,
            valid=False,
            error_code="missing_answer_object",
        )
    return _DelegationOutcome(
        text=str(payload),
        payload=payload,
        should_answer_to_user=None,
        valid=False,
        error_code="unstructured_payload",
    )


def _delegation_result_status(outcome: _DelegationOutcome) -> str:
    if not outcome.valid:
        return "invalid_result"
    if outcome.should_answer_to_user is False:
        return "not_user_answerable"
    return "success"
