from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
import json
from typing import Any, Sequence

from ratchet_sm import FailAction, RetryAction, ValidAction
from ratchet_sm.normalizers.extract_pseudo_tool_call import has_pseudo_tool_call_tag

from minibot.app.runtime_structured_output import RuntimeStructuredOutputValidator
from minibot.app.response_parser import continue_loop_requested
from minibot.core.agent_runtime import (
    AgentMessage,
    AgentState,
    AppendMessageDirective,
    MessagePart,
    RuntimeLimits,
)
from minibot.llm.provider_factory import LLMClient
from minibot.llm.services.continue_loop import CONTINUE_LOOP_RETRY_PATCH
from minibot.llm.services.runtime_message_renderer import RuntimeMessageRenderer
from minibot.llm.services.tool_loop_guard import (
    MAX_REPEATED_TOOL_ITERATIONS,
    any_tool_call_truncated,
    tool_iteration_signature,
    tool_loop_fallback_payload,
)
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.utils import humanize_token_count


_TRUNCATED_PATCH = (
    "Your previous response was truncated. "
    "Please resend your complete tool call with all required arguments."
)
_PSEUDO_TOOL_PATCH = "Please use the tool calling interface instead of embedding tool calls in text."


@dataclass(frozen=True)
class RuntimeResult:
    payload: Any
    response_id: str | None
    state: AgentState
    total_tokens: int = 0


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
        self._tools = list(tools or [])
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
        response_schema: dict[str, Any] | None = None,
        prompt_cache_key: str | None = None,
        initial_previous_response_id: str | None = None,
        structured_validator: RuntimeStructuredOutputValidator | None = None,
    ) -> RuntimeResult:
        tool_calls_count = 0
        step = 0
        previous_response_id: str | None = initial_previous_response_id
        responses_followup_messages: list[dict[str, Any]] | None = None
        total_tokens = 0
        repeated_failure_counts: dict[str, int] = {}
        repeated_iteration_count = 0
        last_iteration_signature: str | None = None
        repeated_continue_loop_count = 0
        last_continue_loop_signature: str | None = None
        _validator = structured_validator if response_schema is not None else None
        if _validator is None and response_schema is not None:
            _validator = RuntimeStructuredOutputValidator(max_attempts=3, schema_model=response_schema)
        truncated_tool_call_count = 0

        async with asyncio.timeout(self._limits.timeout_seconds):
            while True:
                if step >= self._limits.max_steps:
                    return RuntimeResult(
                        payload={
                            "answer": {
                                "kind": "text",
                                "content": "I reached the maximum execution steps before finishing.",
                            },
                            "should_answer_to_user": True,
                            "attachments": [],
                        }
                        if response_schema
                        else "I reached the maximum execution steps before finishing.",
                        response_id=previous_response_id,
                        state=state,
                        total_tokens=total_tokens,
                    )

                call_messages = self._message_renderer.render_messages(state)
                if (
                    self._llm_client.is_responses_provider()
                    and previous_response_id is not None
                    and responses_followup_messages is not None
                ):
                    call_messages = responses_followup_messages

                completion = await self._llm_client.complete_once(
                    messages=call_messages,
                    tools=self._tools,
                    response_schema=response_schema,
                    prompt_cache_key=prompt_cache_key,
                    previous_response_id=previous_response_id,
                )
                if isinstance(completion.total_tokens, int) and completion.total_tokens > 0:
                    total_tokens += completion.total_tokens
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
                                payload=self._repeated_failure_payload(
                                    response_schema=response_schema,
                                    tool_name="truncated_tool_call",
                                ),
                                response_id=completion.response_id,
                                state=state,
                                total_tokens=total_tokens,
                            )
                        state.messages.append(self._message_renderer.from_provider_assistant_message(completion.message))
                        state.messages.append(
                            AgentMessage(role="user", content=[MessagePart(type="text", text=_TRUNCATED_PATCH)])
                        )
                        continue
                    if not tool_calls and has_pseudo_tool_call_tag(raw_message_content):
                        state.messages.append(self._message_renderer.from_provider_assistant_message(completion.message))
                        state.messages.append(
                            AgentMessage(role="user", content=[MessagePart(type="text", text=_PSEUDO_TOOL_PATCH)])
                        )
                        continue
                if not tool_calls:
                    assistant_message = self._message_renderer.from_provider_assistant_message(completion.message)
                    state.messages.append(assistant_message)
                    if _validator is not None:
                        action = _validator.receive(getattr(completion.message, "content", ""))
                        if isinstance(action, ValidAction):
                            payload = _validator.valid_payload(action)
                            if self._tools and continue_loop_requested(payload):
                                continue_signature = json.dumps(
                                    payload,
                                    sort_keys=True,
                                    ensure_ascii=True,
                                    separators=(",", ":"),
                                    default=str,
                                )
                                if continue_signature == last_continue_loop_signature:
                                    repeated_continue_loop_count += 1
                                else:
                                    repeated_continue_loop_count = 1
                                last_continue_loop_signature = continue_signature
                                _validator.reset()
                                if repeated_continue_loop_count >= 2:
                                    self._logger.warning(
                                        "agent runtime repeated identical continue_loop payload; returning fallback",
                                        extra={"step": step, "response_id": completion.response_id},
                                    )
                                    return RuntimeResult(
                                        payload=self._repeated_failure_payload(
                                            response_schema=response_schema,
                                            tool_name="continue_loop",
                                        ),
                                        response_id=completion.response_id,
                                        state=state,
                                        total_tokens=total_tokens,
                                    )
                                self._logger.debug(
                                    "agent runtime structured output requested continue_loop",
                                    extra={"step": step, "response_id": completion.response_id},
                                )
                                state.messages.append(
                                    AgentMessage(
                                        role="user",
                                        content=[MessagePart(type="text", text=CONTINUE_LOOP_RETRY_PATCH)],
                                    )
                                )
                                continue
                            self._logger.debug(
                                "agent runtime structured output validated",
                                extra={
                                    "step": step,
                                    "response_id": completion.response_id,
                                    "attempts": action.attempts,
                                    "format_detected": action.format_detected,
                                },
                            )
                            return RuntimeResult(
                                payload=payload,
                                response_id=completion.response_id,
                                state=state,
                                total_tokens=total_tokens,
                            )
                        if isinstance(action, RetryAction):
                            retry_patch = (action.prompt_patch or "").strip()
                            raw_preview = action.raw.strip().replace("\n", " ")
                            if len(raw_preview) > 300:
                                raw_preview = f"{raw_preview[:300]}..."
                            self._logger.warning(
                                "agent runtime structured output invalid; retrying",
                                extra={
                                    "step": step,
                                    "response_id": completion.response_id,
                                    "attempts": action.attempts,
                                    "reason": action.reason,
                                    "errors": list(action.errors),
                                    "raw_preview": raw_preview,
                                    "retry_patch_present": bool(retry_patch),
                                },
                            )
                            if retry_patch:
                                state.messages.append(
                                    AgentMessage(
                                        role="user",
                                        content=[MessagePart(type="text", text=retry_patch)],
                                    )
                                )
                            continue
                        if isinstance(action, FailAction):
                            validation_errors: list[str] = []
                            for item in action.history:
                                if isinstance(item, RetryAction):
                                    validation_errors.extend(item.errors)
                            raw_preview = action.raw.strip().replace("\n", " ")
                            if len(raw_preview) > 300:
                                raw_preview = f"{raw_preview[:300]}..."
                            self._logger.warning(
                                "agent runtime structured output failed; returning fallback",
                                extra={
                                    "step": step,
                                    "response_id": completion.response_id,
                                    "attempts": action.attempts,
                                    "reason": action.reason,
                                    "errors": validation_errors,
                                    "raw_preview": raw_preview,
                                },
                            )
                            return RuntimeResult(
                                payload=_validator.fallback_payload(),
                                response_id=completion.response_id,
                                state=state,
                                total_tokens=total_tokens,
                            )
                    self._logger.debug(
                        "agent runtime step returned final assistant message",
                        extra={"step": step, "response_id": completion.response_id},
                    )
                    return RuntimeResult(
                        payload=getattr(completion.message, "content", ""),
                        response_id=completion.response_id,
                        state=state,
                        total_tokens=total_tokens,
                    )

                tool_calls_count += len(tool_calls)
                self._logger.debug(
                    "agent runtime step requested tool calls",
                    extra={
                        "step": step,
                        "response_id": completion.response_id,
                        "tool_calls": len(tool_calls),
                    },
                )
                if tool_calls_count > self._limits.max_tool_calls:
                    return RuntimeResult(
                        payload={
                            "answer": {
                                "kind": "text",
                                "content": "I reached the maximum number of tool calls before finishing.",
                            },
                            "should_answer_to_user": True,
                            "attachments": [],
                        }
                        if response_schema
                        else "I reached the maximum number of tool calls before finishing.",
                        response_id=completion.response_id,
                        state=state,
                        total_tokens=total_tokens,
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
                    self._logger.debug(
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
                                    payload=self._repeated_failure_payload(
                                        response_schema=response_schema,
                                        tool_name=execution.tool_name,
                                    ),
                                    response_id=completion.response_id,
                                    state=state,
                                    total_tokens=total_tokens,
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
                            response_schema,
                        ),
                        response_id=completion.response_id,
                        state=state,
                        total_tokens=total_tokens,
                    )
                step += 1

    @staticmethod
    def _is_repeated_failure_candidate(content: Any) -> bool:
        if not isinstance(content, dict):
            return False
        if content.get("ok") is not False:
            return False
        return bool(content.get("is_repeated_failure_candidate"))

    @staticmethod
    def _repeated_failure_payload(*, response_schema: dict[str, Any] | None, tool_name: str) -> Any:
        answer = (
            "I hit the same tool error repeatedly with the same parameters before finishing. "
            f"Tool: {tool_name}. Please adjust parameters or ask for a different approach."
        )
        if response_schema is None:
            return answer
        return {
            "answer": {
                "kind": "text",
                "content": answer,
            },
            "should_answer_to_user": True,
            "attachments": [],
        }

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
