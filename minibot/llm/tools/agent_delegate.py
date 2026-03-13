from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from llm_async.models import Tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from minibot.app.agent_policies import filter_tools_for_agent
from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_runtime import AgentRuntime
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.runtime_structured_output import RuntimeStructuredOutputValidator
from minibot.app.runtime_limits import build_runtime_limits
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.core.agents import AgentSpec
from minibot.llm.services import LLMExecutionProfile
from minibot.llm.services.response_schemas import delegated_agent_response_schema
from minibot.llm.tools.arg_utils import optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import strict_object, string_field
from minibot.shared.assistant_response import (
    validate_attachments,
)
from minibot.shared.parse_utils import parse_json_with_fenced_fallback
from minibot.shared.utils import session_identifier


@dataclass(frozen=True)
class _DelegationOutcome:
    text: str
    payload: Any
    should_continue: bool | None
    valid: bool
    error_code: str | None
    attachments: list[dict[str, Any]]


class _DelegatedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "html", "markdown", "json"]
    content: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("meta", mode="before")
    @classmethod
    def _normalize_meta(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str | None) -> str | None:
        return value


class _DelegatedPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: _DelegatedAnswer | None = None
    should_continue: bool = False
    attachments: list[Any] = Field(default_factory=list)

    @field_validator("attachments", mode="before")
    @classmethod
    def _normalize_attachments(cls, value: Any) -> Any:
        if value is None:
            return []
        return value

    @model_validator(mode="after")
    def _validate_terminal_visibility(self) -> "_DelegatedPayload":
        if self.should_continue:
            return self
        content = self.answer.content if self.answer is not None else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError(
                "final delegated responses with should_continue=false must include a non-empty answer.content"
            )
        return self


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
            ToolBinding(tool=self._fetch_agent_info_schema(), handler=self._fetch_agent_info),
            ToolBinding(tool=self._invoke_agent_schema(), handler=self._invoke_agent),
        ]

    def _fetch_agent_info_schema(self) -> Tool:
        return Tool(
            name="fetch_agent_info",
            description=load_tool_description("fetch_agent_info"),
            parameters=strict_object(
                properties={
                    "agent_name": string_field("Exact specialist name from the available specialists list."),
                },
                required=["agent_name"],
            ),
        )

    def _invoke_agent_schema(self) -> Tool:
        return Tool(
            name="invoke_agent",
            description=load_tool_description("invoke_agent"),
            parameters=strict_object(
                properties={
                    "agent_name": string_field("Exact specialist name."),
                    "task": string_field("Concrete delegated task for the specialist."),
                    "context": string_field("Optional supporting context for the specialist."),
                },
                required=["agent_name", "task"],
            ),
        )

    async def _fetch_agent_info(self, payload: dict[str, object], __: ToolContext) -> dict[str, object]:
        agent_name = require_non_empty_str(payload, "agent_name")
        spec = self._registry.get(agent_name)
        if spec is None:
            return {
                "ok": False,
                "agent": agent_name,
                "error_code": "agent_not_found",
                "error": f"agent '{agent_name}' is not available",
            }
        return {
            "ok": True,
            "agent": spec.name,
            "name": spec.name,
            "description": spec.description,
            "system_prompt": spec.system_prompt,
        }

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
                structured_validator=RuntimeStructuredOutputValidator(schema_model=_DelegatedPayload, max_attempts=3),
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
                    structured_validator=RuntimeStructuredOutputValidator(
                        schema_model=_DelegatedPayload,
                        max_attempts=3,
                    ),
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
                        "should_continue": False,
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
                "should_continue": outcome.should_continue,
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
        return [binding for binding in scoped if binding.tool.name not in {"invoke_agent", "fetch_agent_info"}]

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
    return delegated_agent_response_schema()


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
                should_continue=None,
                valid=False,
                error_code="invalid_payload_schema",
                attachments=_validate_attachments(payload_obj.get("attachments")),
            )
        attachments = _validate_attachments(parsed.attachments)
        answer_text = ""
        if parsed.answer is not None and isinstance(parsed.answer.content, str):
            answer_text = parsed.answer.content
        return _DelegationOutcome(
            text=answer_text,
            payload=parsed.model_dump(mode="python", exclude_none=True),
            should_continue=parsed.should_continue,
            valid=True,
            error_code=None,
            attachments=attachments,
        )
    return _DelegationOutcome(
        text=str(payload),
        payload=payload,
        should_continue=None,
        valid=False,
        error_code="unstructured_payload",
        attachments=[],
    )


def _delegation_result_status(outcome: _DelegationOutcome) -> str:
    if not outcome.valid:
        return "invalid_result"
    if outcome.should_continue is True:
        return "continue"
    return "success"


def _agent_prompt_cache_key(*, llm_client: Any, context: ToolContext, agent_name: str) -> str | None:
    profile = LLMExecutionProfile.from_client(llm_client)
    if not profile.prompt_cache_enabled:
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
