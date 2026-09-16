from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from minibot.app.response_parser import extract_pre_response_meta
from minibot.core.agent_runtime import (
    AgentMessage,
    AgentState,
    AppendMessageDirective,
    MessagePart,
    RuntimeLimits,
)
from minibot.core.events import ReasoningEvent
from minibot.core.tasks import TaskStopReason
from minibot.llm.provider_factory import LLMClient
from minibot.llm.services.reasoning_replay import reasoning_text_from_message
from minibot.llm.services.runtime_compaction import RuntimeCompactor
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

if TYPE_CHECKING:  # pragma: no cover
    from minibot.app.event_bus import EventBus


def _has_pseudo_tool_call_tag(text: str) -> bool:
    return "<tool_call>" in text


_TRUNCATED_PATCH = (
    "Your previous response was truncated. Please resend your complete tool call with all required arguments."
)
_PSEUDO_TOOL_PATCH = "Please use the tool calling interface instead of embedding tool calls in text."
_CONTINUE_AFTER_COMPACTION = "Continue the task from the summary above. Do not repeat completed steps."
_REPEATED_FAILURE_NUDGE_AT = 2
_REPEATED_FAILURE_NUDGE = (
    "The tool `{tool}` just failed again with the same arguments and the same error, so that call "
    "cannot succeed. Do not retry it. Carry on with whatever else the task needs, and state this "
    "unresolved failure plainly in your final answer so the person can decide what to do about it."
)


@dataclass(frozen=True)
class RuntimeResult:
    payload: Any
    response_id: str | None
    state: AgentState
    total_tokens: int = 0
    input_tokens: int | None = None
    provider_tool_calls: int = 0
    pre_response_meta: dict[str, Any] | None = field(default=None)
    stop_reason: TaskStopReason = TaskStopReason.COMPLETED


class AgentRuntime:
    def __init__(
        self,
        llm_client: LLMClient,
        tools: Sequence[ToolBinding] | None = None,
        limits: RuntimeLimits | None = None,
        allowed_append_message_tools: Sequence[str] | None = None,
        allow_system_inserts: bool = False,
        managed_files_root: str | None = None,
        event_bus: EventBus | None = None,
        compactor: RuntimeCompactor | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._tools = [pre_response_binding(), *list(tools or [])]
        self._limits = limits or RuntimeLimits()
        # Only delegated runs pass one: the main turn compacts its own persisted history between
        # turns, in HistoryCompactionService, and never loops here long enough to need this.
        self._compactor = compactor
        self._allow_system_inserts = allow_system_inserts
        self._allowed_append_message_tools = set(allowed_append_message_tools or [])
        self._event_bus = event_bus
        self._logger = logging.getLogger("minibot.agent_runtime")
        self._message_renderer = RuntimeMessageRenderer(
            media_input_mode=llm_client.media_input_mode(),
            is_responses_provider=llm_client.is_responses_provider(),
            managed_files_root=managed_files_root,
            logger=self._logger,
        )

    async def _publish_reasoning(self, message: Any, *, step: int, tool_context: ToolContext) -> None:
        """Telemetry must never break a turn: a stopped bus raises, and shutdown races are normal."""
        if self._event_bus is None:
            return
        text = reasoning_text_from_message(message)
        if not text:
            return
        try:
            await self._event_bus.publish(
                ReasoningEvent(
                    text=text,
                    step=step,
                    turn_id=tool_context.turn_id,
                    owner_id=tool_context.owner_id,
                    channel=tool_context.channel,
                    chat_id=tool_context.chat_id,
                )
            )
        except Exception:  # noqa: BLE001
            self._logger.debug("reasoning event publish failed", extra={"step": step}, exc_info=True)

    async def run(
        self,
        state: AgentState,
        tool_context: ToolContext,
        prompt_cache_key: str | None = None,
        initial_previous_response_id: str | None = None,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> RuntimeResult:
        tool_calls_count = 0
        step = 0
        previous_response_id: str | None = initial_previous_response_id
        # Some Responses-shaped providers (e.g. codex, which forces store=False on the backend)
        # can't rely on server-side state, so they never get the delta-only previous_response_id
        # follow-up path — every step resends the full rendered history instead.
        use_responses_followup = (
            self._llm_client.is_responses_provider()
            and self._llm_client.responses_state_mode() == "previous_response_id"
        )
        responses_followup_messages: list[dict[str, Any]] | None = None
        total_tokens = 0
        input_tokens: int | None = None
        provider_tool_calls = 0
        repeated_failure_counts: dict[str, int] = {}
        repeated_iteration_count = 0
        last_iteration_signature: str | None = None
        truncated_tool_call_count = 0

        async with asyncio.timeout(self._limits.timeout_seconds):
            while True:
                if self._limits.max_steps is not None and step >= self._limits.max_steps:
                    return RuntimeResult(
                        payload="I reached the maximum execution steps before finishing.",
                        response_id=previous_response_id,
                        state=state,
                        total_tokens=total_tokens,
                        input_tokens=input_tokens,
                        provider_tool_calls=provider_tool_calls,
                        stop_reason=TaskStopReason.MAX_STEPS,
                    )

                # Top of the loop on purpose: every tool call from the previous step already has
                # its result appended, so the transcript is consistent and rewriting it can't
                # orphan a tool_call the provider is still expecting an answer for.
                if self._compactor is not None and self._compactor.should_compact(input_tokens):
                    outcome = await self._compactor.compact(
                        state,
                        previous_response_id=previous_response_id,
                        prompt_cache_key=prompt_cache_key,
                    )
                    if outcome.performed:
                        self._logger.info(
                            "agent runtime compacted context mid-run",
                            extra={
                                "step": step,
                                "input_tokens_before": input_tokens,
                                "threshold_tokens": self._compactor.threshold_tokens,
                                "message_count": len(state.messages),
                                "native": outcome.response_id is not None,
                            },
                        )
                        previous_response_id = outcome.response_id
                        if use_responses_followup and previous_response_id is not None:
                            # The compacted response already holds this state server-side, the
                            # same reason build_continue_call_kwargs sends a minimal nudge rather
                            # than full history to continue a previous_response_id: resending the
                            # local render here would duplicate it right after compacting
                            # specifically to shrink it.
                            responses_followup_messages = self._message_renderer.render_messages(
                                AgentState(
                                    messages=[
                                        AgentMessage(
                                            role="user",
                                            content=[MessagePart(type="text", text=_CONTINUE_AFTER_COMPACTION)],
                                        )
                                    ]
                                )
                            )
                        else:
                            responses_followup_messages = None
                    # Either way, wait for a fresh measurement before considering it again.
                    input_tokens = None

                call_messages = self._message_renderer.render_messages(state)
                if (
                    use_responses_followup
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
                        previous_response_id=previous_response_id if use_responses_followup else None,
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
                await self._publish_reasoning(completion.message, step=step, tool_context=tool_context)
                if isinstance(completion.total_tokens, int) and completion.total_tokens > 0:
                    total_tokens += completion.total_tokens
                input_tokens = completion.input_tokens
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
                if progress_callback is not None:
                    await progress_callback(
                        {
                            "phase": "provider",
                            "step": step,
                            "tool_calls": tool_calls_count,
                            "total_tokens": total_tokens,
                        }
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
                                input_tokens=input_tokens,
                                provider_tool_calls=provider_tool_calls,
                                stop_reason=TaskStopReason.TRUNCATED_TOOL_CALL,
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
                        input_tokens=input_tokens,
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
                if self._limits.max_tool_calls is not None and tool_calls_count > self._limits.max_tool_calls:
                    return RuntimeResult(
                        payload="I reached the maximum number of tool calls before finishing.",
                        response_id=completion.response_id,
                        state=state,
                        total_tokens=total_tokens,
                        input_tokens=input_tokens,
                        provider_tool_calls=provider_tool_calls,
                        stop_reason=TaskStopReason.MAX_TOOL_CALLS,
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
                if progress_callback is not None:
                    await progress_callback(
                        {
                            "phase": "tools",
                            "step": step,
                            "tool_calls": tool_calls_count,
                            "total_tokens": total_tokens,
                            "tool_names": [execution.tool_name for execution in executions],
                        }
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
                            # Nudge once per distinct failure, then let the run continue. Killing it
                            # here used to throw away everything the run had already produced over a
                            # call that simply cannot succeed -- an unreachable host is a finding to
                            # report, not a reason to lose the work. max_steps, max_tool_calls and
                            # the timeout are still the ceilings that stop a genuine loop.
                            if count == _REPEATED_FAILURE_NUDGE_AT:
                                self._logger.warning(
                                    "agent runtime repeated identical tool failure; telling the agent to move on",
                                    extra={
                                        "tool": execution.tool_name,
                                        "call_id": execution.call_id,
                                        "failure_count": count,
                                        "failure_signature": failure_signature[:16],
                                    },
                                )
                                nudge = AgentMessage(
                                    role="user",
                                    content=[
                                        MessagePart(
                                            type="text",
                                            text=_REPEATED_FAILURE_NUDGE.format(tool=execution.tool_name),
                                        )
                                    ],
                                )
                                state.messages.append(nudge)
                                # Also through the directive list: in previous_response_id mode only
                                # these get rendered into the follow-up, so appending to state alone
                                # would leave the model never seeing it.
                                applied_directive_messages.append(nudge)
                if use_responses_followup:
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
                        input_tokens=input_tokens,
                        provider_tool_calls=provider_tool_calls,
                        stop_reason=TaskStopReason.REPEATED_ITERATION,
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
