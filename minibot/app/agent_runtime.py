from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from minibot.app.response_parser import extract_pre_response_meta
from minibot.core.agent_runtime import (
    AgentMessage,
    AgentState,
    AppendMessageDirective,
    MessagePart,
    RuntimeLimits,
)
from minibot.llm.provider_factory import LLMClient
from minibot.llm.services.runtime_message_renderer import RuntimeMessageRenderer
from minibot.llm.services.tool_loop_guard import (
    MAX_REPEATED_TOOL_ITERATIONS,
    any_tool_call_truncated,
    tool_iteration_signature,
    tool_loop_fallback_payload,
)
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.pre_response import pre_response_binding
from minibot.shared.utils import humanize_token_count


def _has_pseudo_tool_call_tag(text: str) -> bool:
    return "<tool_call>" in text


_TRUNCATED_PATCH = (
    "Your previous response was truncated. Please resend your complete tool call with all required arguments."
)
_PSEUDO_TOOL_PATCH = "Please use the tool calling interface instead of embedding tool calls in text."


@dataclass(frozen=True)
class RuntimeResult:
    payload: Any
    response_id: str | None
    state: AgentState
    total_tokens: int = 0
    provider_tool_calls: int = 0
    pre_response_meta: dict[str, Any] | None = field(default=None)


class AgentRuntime:
    def __init__(
        self,
        llm_client: LLMClient,
        tools: Sequence[ToolBinding] | None = None,
        limits: RuntimeLimits | None = None,
        allowed_append_message_tools: Sequence[str] | None = None,
        allow_system_inserts: bool = False,
        managed_files_root: str | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._tools = [pre_response_binding(), *list(tools or [])]
        self._limits = limits or RuntimeLimits()
        self._allow_system_inserts = allow_system_inserts
        self._allowed_append_message_tools = set(allowed_append_message_tools or [])
        self._logger = logging.getLogger("minibot.agent_runtime")
        self._message_renderer = RuntimeMessageRenderer(
            media_input_mode=llm_client.media_input_mode(),
            managed_files_root=managed_files_root,
            logger=self._logger,
        )

    async def run(
        self,
        state: AgentState,
        tool_context: ToolContext,
        prompt_cache_key: str | None = None,
        initial_previous_response_id: str | None = None,
    ) -> RuntimeResult:
        tool_calls_count = 0
        step = 0
        previous_response_id: str | None = initial_previous_response_id
        responses_followup_messages: list[dict[str, Any]] | None = None
        total_tokens = 0
        provider_tool_calls = 0
        repeated_failure_counts: dict[str, int] = {}
        repeated_iteration_count = 0
        last_iteration_signature: str | None = None
        truncated_tool_call_count = 0

        async with asyncio.timeout(self._limits.timeout_seconds):
            while True:
                if step >= self._limits.max_steps:
                    return RuntimeResult(
                        payload="I reached the maximum execution steps before finishing.",
                        response_id=previous_response_id,
                        state=state,
                        total_tokens=total_tokens,
                        provider_tool_calls=provider_tool_calls,
                    )

                call_messages = self._message_renderer.render_messages(state)
                if (
                    self._llm_client.is_responses_provider()
                    and previous_response_id is not None
                    and responses_followup_messages is not None
                ):
                    call_messages = responses_followup_messages

                provider_name = getattr(self._llm_client, "provider_name", lambda: "unknown")()
                started_at = time.monotonic()
                self._logger.debug(
                    "agent runtime provider step started",
                    extra={
                        "step": step,
                        "provider": provider_name,
                        "previous_response_id_present": previous_response_id is not None,
                        "message_count": len(call_messages),
                    },
                )
                try:
                    completion = await self._llm_client.complete_once(
                        messages=call_messages,
                        tools=self._tools,
                        prompt_cache_key=prompt_cache_key,
                        previous_response_id=previous_response_id,
                    )
                except Exception:
                    self._logger.warning(
                        "agent runtime provider step failed",
                        extra={
                            "step": step,
                            "provider": provider_name,
                            "previous_response_id_present": previous_response_id is not None,
                            "duration_ms": round((time.monotonic() - started_at) * 1000),
                        },
                        exc_info=True,
                    )
                    raise
                if isinstance(completion.total_tokens, int) and completion.total_tokens > 0:
                    total_tokens += completion.total_tokens
                if isinstance(completion.provider_tool_calls, int) and completion.provider_tool_calls > 0:
                    provider_tool_calls += completion.provider_tool_calls
                responses_followup_messages = None
                self._logger.debug(
                    "agent runtime provider step completed",
                    extra={
                        "step": step,
                        "response_id": completion.response_id,
                        "message_count": len(state.messages),
                        "step_tokens": humanize_token_count(completion.total_tokens)
                        if isinstance(completion.total_tokens, int)
                        else "0",
                        "runtime_total_tokens": humanize_token_count(total_tokens),
                        "provider": provider_name,
                        "duration_ms": round((time.monotonic() - started_at) * 1000),
                    },
                )
                previous_response_id = completion.response_id

                tool_calls = list(getattr(completion.message, "tool_calls", None) or [])
                if self._tools:
                    raw_message_content = (
                        completion.message.content if isinstance(completion.message.content, str) else ""
                    )
                    if tool_calls and any_tool_call_truncated(tool_calls):
                        truncated_tool_call_count += 1
                        if truncated_tool_call_count >= 3:
                            return RuntimeResult(
                                payload=(
                                    "I hit a truncated tool call error repeatedly before finishing. "
                                    "Please try again or rephrase your request."
                                ),
                                response_id=completion.response_id,
                                state=state,
                                total_tokens=total_tokens,
                                provider_tool_calls=provider_tool_calls,
                            )
                        state.messages.append(
                            self._message_renderer.from_provider_assistant_message(completion.message)
                        )
                        state.messages.append(
                            AgentMessage(role="user", content=[MessagePart(type="text", text=_TRUNCATED_PATCH)])
                        )
                        continue
                    if not tool_calls and _has_pseudo_tool_call_tag(raw_message_content):
                        state.messages.append(
                            self._message_renderer.from_provider_assistant_message(completion.message)
                        )
                        state.messages.append(
                            AgentMessage(role="user", content=[MessagePart(type="text", text=_PSEUDO_TOOL_PATCH)])
                        )
                        continue
                if not tool_calls:
                    assistant_message = self._message_renderer.from_provider_assistant_message(completion.message)
                    state.messages.append(assistant_message)
                    self._logger.debug(
                        "agent runtime step returned final assistant message",
                        extra={"step": step, "response_id": completion.response_id},
                    )
                    return RuntimeResult(
                        payload=getattr(completion.message, "content", ""),
                        response_id=completion.response_id,
                        state=state,
                        total_tokens=total_tokens,
                        provider_tool_calls=provider_tool_calls,
                        pre_response_meta=extract_pre_response_meta(state),
                    )

                tool_calls_count += len(tool_calls)
                self._logger.info(
                    "agent runtime step requested tool calls",
                    extra={
                        "step": step,
                        "response_id": completion.response_id,
                        "tool_calls": len(tool_calls),
                    },
                )
                if tool_calls_count > self._limits.max_tool_calls:
                    return RuntimeResult(
                        payload="I reached the maximum number of tool calls before finishing.",
                        response_id=completion.response_id,
                        state=state,
                        total_tokens=total_tokens,
                        provider_tool_calls=provider_tool_calls,
                    )

                state.messages.append(
                    self._message_renderer.from_provider_assistant_tool_call_message(completion.message)
                )
                executions = await self._llm_client.execute_tool_calls_for_runtime(
                    tool_calls,
                    self._tools,
                    tool_context,
                    responses_mode=self._llm_client.is_responses_provider(),
                )
                applied_directive_messages: list[AgentMessage] = []
                for execution in executions:
                    self._logger.info(
                        "agent runtime tool execution result",
                        extra={
                            "tool": execution.tool_name,
                            "call_id": execution.call_id,
                            "directives_count": len(execution.result.directives),
                        },
                    )
                    state.messages.append(
                        AgentMessage(
                            role="tool",
                            name=execution.tool_name,
                            tool_call_id=execution.call_id,
                            content=[MessagePart(type="json", value=execution.result.content)],
                        )
                    )
                    applied_directive_messages.extend(
                        self._apply_directives(state, execution.tool_name, execution.result.directives)
                    )
                    if self._is_repeated_failure_candidate(execution.result.content):
                        failure_signature = str(execution.result.content.get("failure_signature", "")).strip()
                        if failure_signature:
                            count = repeated_failure_counts.get(failure_signature, 0) + 1
                            repeated_failure_counts[failure_signature] = count
                            if count >= 2:
                                self._logger.warning(
                                    "agent runtime repeated identical tool failure; returning fallback",
                                    extra={
                                        "tool": execution.tool_name,
                                        "call_id": execution.call_id,
                                        "failure_count": count,
                                        "failure_signature": failure_signature[:16],
                                    },
                                )
                                return RuntimeResult(
                                    payload=(
                                        "I hit the same tool error repeatedly with the same parameters before "
                                        f"finishing. Tool: {execution.tool_name}. "
                                        "Please adjust parameters or ask for a different approach."
                                    ),
                                    response_id=completion.response_id,
                                    state=state,
                                    total_tokens=total_tokens,
                                    provider_tool_calls=provider_tool_calls,
                                )
                if self._llm_client.is_responses_provider():
                    responses_followup_messages = [execution.message_payload for execution in executions]
                    if applied_directive_messages:
                        responses_followup_messages.extend(
                            self._message_renderer.render_messages(AgentState(messages=applied_directive_messages))
                        )
                iteration_signature = tool_iteration_signature(
                    tool_calls,
                    [execution.message_payload for execution in executions],
                )
                if iteration_signature and iteration_signature == last_iteration_signature:
                    repeated_iteration_count += 1
                else:
                    repeated_iteration_count = 1
                last_iteration_signature = iteration_signature
                if repeated_iteration_count >= MAX_REPEATED_TOOL_ITERATIONS:
                    self._logger.warning(
                        "agent runtime repeated identical tool outputs; returning fallback",
                        extra={
                            "step": step,
                            "response_id": completion.response_id,
                            "repeated_count": repeated_iteration_count,
                        },
                    )
                    return RuntimeResult(
                        payload=tool_loop_fallback_payload(
                            [execution.message_payload for execution in executions],
                            [execution.tool_name for execution in executions],
                        ),
                        response_id=completion.response_id,
                        state=state,
                        total_tokens=total_tokens,
                        provider_tool_calls=provider_tool_calls,
                    )
                step += 1

    @staticmethod
    def _is_repeated_failure_candidate(content: Any) -> bool:
        if not isinstance(content, dict):
            return False
        if content.get("ok") is not False:
            return False
        return bool(content.get("is_repeated_failure_candidate"))

    def _apply_directives(self, state: AgentState, tool_name: str, directives: Sequence[Any]) -> list[AgentMessage]:
        appended: list[AgentMessage] = []
        for directive in directives:
            if isinstance(directive, AppendMessageDirective):
                if tool_name not in self._allowed_append_message_tools:
                    self._logger.warning(
                        "ignored append_message directive from untrusted tool",
                        extra={"tool": tool_name},
                    )
                    continue
                if directive.message.role == "system" and not self._allow_system_inserts:
                    self._logger.warning("ignored system append_message directive", extra={"tool": tool_name})
                    continue
                stamped_message = AgentMessage(
                    role=directive.message.role,
                    content=directive.message.content,
                    name=directive.message.name,
                    tool_call_id=directive.message.tool_call_id,
                    raw_content=directive.message.raw_content,
                    metadata={
                        **directive.message.metadata,
                        "synthetic": True,
                        "source_tool": tool_name,
                    },
                )
                state.messages.append(stamped_message)
                self._logger.debug(
                    "applied append_message directive",
                    extra={
                        "tool": tool_name,
                        "role": stamped_message.role,
                        "parts_count": len(stamped_message.content),
                    },
                )
                appended.append(stamped_message)
        return appended
