from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from llm_async.models import Tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from minibot.app.agent_policies import filter_tools_for_agent
from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_runtime import AgentRuntime
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.runtime_limits import build_runtime_limits
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.core.agents import AgentSpec
from minibot.llm.tools.arg_utils import optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import empty_object_schema, strict_object, string_field
from minibot.shared.parse_utils import parse_json_with_fenced_fallback
from minibot.shared.utils import session_identifier
from minibot.shared.assistant_response import (
    assistant_response_schema,
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


class _DelegatedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "html", "markdown", "json"]
    content: str
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("meta", mode="before")
    @classmethod
    def _normalize_meta(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must be a non-empty string")
        return value


class _DelegatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: _DelegatedAnswer
    should_answer_to_user: bool
    attachments: list[Any] = Field(default_factory=list)

    @field_validator("attachments", mode="before")
    @classmethod
    def _normalize_attachments(cls, value: Any) -> Any:
        if value is None:
            return []
        return value


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
            description=load_tool_description("list_agents"),
            parameters=empty_object_schema(),
        )

    def _invoke_agent_schema(self) -> Tool:
        return Tool(
            name="invoke_agent",
            description=load_tool_description("invoke_agent"),
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
        tool_required = self._delegated_tool_call_required(spec)
        attempts = 1
        state = self._build_state(spec=spec, task=task, details=details)
        total_tokens = 0
        previous_response_id: str | None = None
        prompt_cache_key = _agent_prompt_cache_key(llm_client=llm_client, context=context, agent_name=spec.name)
        state_mode_getter = getattr(llm_client, "responses_state_mode", None)
        use_previous_response_id = callable(state_mode_getter) and state_mode_getter() == "previous_response_id"
        try:
            generation = await runtime.run(
                state=state,
                tool_context=context,
                response_schema=_agent_response_schema(),
                prompt_cache_key=prompt_cache_key,
                initial_previous_response_id=previous_response_id,
            )
            if use_previous_response_id:
                previous_response_id = generation.response_id
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
                    prompt_cache_key=prompt_cache_key,
                    initial_previous_response_id=previous_response_id,
                )
                if use_previous_response_id:
                    previous_response_id = generation.response_id
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
            if outcome.attachments:
                self._logger.debug(
                    "delegation returned attachments",
                    extra={
                        "agent": spec.name,
                        "attachment_count": len(outcome.attachments),
                        "attachment_types": [a.get("type") for a in outcome.attachments],
                    },
                )
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
                "attachments": outcome.attachments,
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


def _agent_response_schema() -> dict[str, Any]:
    return assistant_response_schema(kinds=["text", "html", "markdown", "json"], include_attachments=True)


def _validate_attachments(raw_attachments: Any) -> list[dict[str, Any]]:
    return validate_attachments(raw_attachments)


def _extract_outcome(payload: Any) -> _DelegationOutcome:
    payload_obj = _payload_to_object(payload)
    if payload_obj is not None:
        try:
            parsed = _DelegatedPayload.model_validate(payload_obj)
        except ValidationError:
            return _DelegationOutcome(
                text=str(payload_obj),
                payload=payload_obj,
                should_answer_to_user=None,
                valid=False,
                error_code="invalid_payload_schema",
                attachments=_validate_attachments(payload_obj.get("attachments")),
            )
        attachments = _validate_attachments(parsed.attachments)
        return _DelegationOutcome(
            text=parsed.answer.content,
            payload=parsed.model_dump(mode="python", exclude_none=True),
            should_answer_to_user=parsed.should_answer_to_user,
            valid=True,
            error_code=None,
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


def _agent_prompt_cache_key(*, llm_client: Any, context: ToolContext, agent_name: str) -> str | None:
    enabled_getter = getattr(llm_client, "prompt_cache_enabled", None)
    if callable(enabled_getter) and not bool(enabled_getter()):
        return None
    channel = context.channel or "agent"
    session_id = session_identifier(channel, context.chat_id, context.user_id)
    return f"{session_id}:agent:{agent_name}"


def _payload_to_object(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        return None
    try:
        parsed = parse_json_with_fenced_fallback(payload)
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None
