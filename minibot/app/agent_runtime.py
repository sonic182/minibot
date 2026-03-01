from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ratchet_sm import FailAction, RetryAction, ValidAction

from minibot.app.runtime_structured_output import RuntimeStructuredOutputValidator
from minibot.core.agent_runtime import (
    AgentMessage,
    AgentState,
    AppendMessageDirective,
    MessagePart,
    RuntimeLimits,
)
from minibot.llm.provider_factory import LLMClient
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.utils import humanize_token_count


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
        self._managed_files_root = Path(managed_files_root).resolve() if managed_files_root else None
        self._logger = logging.getLogger("minibot.agent_runtime")

    async def run(
        self,
        state: AgentState,
        tool_context: ToolContext,
        response_schema: dict[str, Any] | None = None,
        prompt_cache_key: str | None = None,
        initial_previous_response_id: str | None = None,
    ) -> RuntimeResult:
        tool_calls_count = 0
        step = 0
        previous_response_id: str | None = initial_previous_response_id
        responses_followup_messages: list[dict[str, Any]] | None = None
        total_tokens = 0
        structured_validator = RuntimeStructuredOutputValidator(max_attempts=3) if response_schema else None

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
                        }
                        if response_schema
                        else "I reached the maximum execution steps before finishing.",
                        response_id=previous_response_id,
                        state=state,
                        total_tokens=total_tokens,
                    )

                call_messages = self._render_messages(state)
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

                tool_calls = getattr(completion.message, "tool_calls", None) or []
                if not tool_calls:
                    assistant_message = self._from_provider_assistant_message(completion.message)
                    state.messages.append(assistant_message)
                    if structured_validator is not None:
                        action = structured_validator.receive(getattr(completion.message, "content", ""))
                        if isinstance(action, ValidAction):
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
                                payload=structured_validator.valid_payload(action),
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
                            step += 1
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
                                payload=structured_validator.fallback_payload(),
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
                        }
                        if response_schema
                        else "I reached the maximum number of tool calls before finishing.",
                        response_id=completion.response_id,
                        state=state,
                        total_tokens=total_tokens,
                    )

                state.messages.append(self._from_provider_assistant_tool_call_message(completion.message))
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
                if self._llm_client.is_responses_provider():
                    responses_followup_messages = [execution.message_payload for execution in executions]
                    if applied_directive_messages:
                        responses_followup_messages.extend(
                            self._render_messages(AgentState(messages=applied_directive_messages))
                        )
                step += 1

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

    @staticmethod
    def _from_provider_assistant_message(message: Any) -> AgentMessage:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return AgentMessage(role="assistant", content=[MessagePart(type="text", text=content)])
        if isinstance(content, list):
            parts: list[MessagePart] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "text"))
                if part_type in {"text", "input_text"}:
                    parts.append(MessagePart(type="text", text=str(part.get("text", ""))))
                    continue
                if part_type in {"json", "output_json"}:
                    parts.append(MessagePart(type="json", value=part.get("value")))
                    continue
                parts.append(MessagePart(type="json", value=part))
            return AgentMessage(role="assistant", content=parts or [MessagePart(type="text", text="")])
        return AgentMessage(role="assistant", content=[MessagePart(type="text", text=str(content))])

    @staticmethod
    def _from_provider_assistant_tool_call_message(message: Any) -> AgentMessage:
        content = getattr(message, "content", "")
        text = content if isinstance(content, str) else ""
        tool_calls = getattr(message, "tool_calls", None)
        metadata: dict[str, Any] = {}
        if tool_calls:
            metadata["tool_calls"] = [
                {
                    "id": call.id,
                    "type": call.type,
                    "function": call.function,
                    "name": call.name,
                    "input": call.input,
                }
                for call in tool_calls
            ]
        return AgentMessage(role="assistant", content=[MessagePart(type="text", text=text)], metadata=metadata)

    def _render_messages(self, state: AgentState) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for message in state.messages:
            if message.role == "tool":
                messages.append(
                    {
                        "role": "tool",
                        "name": message.name or "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": self._stringify_parts(message.content),
                    }
                )
                continue

            payload: dict[str, Any] = {
                "role": message.role,
                "content": message.raw_content
                if message.raw_content is not None
                else self._render_non_tool_content(message.content),
            }
            tool_calls = message.metadata.get("tool_calls") if message.metadata else None
            if tool_calls:
                payload["tool_calls"] = tool_calls
            messages.append(payload)
        return messages

    def _render_non_tool_content(self, parts: Sequence[MessagePart]) -> str | list[dict[str, Any]]:
        if len(parts) == 1 and parts[0].type == "text" and parts[0].text is not None:
            return parts[0].text
        rendered: list[dict[str, Any]] = []
        provider_mode = self._llm_client.media_input_mode()
        for part in parts:
            if part.type == "text":
                text_value = part.text or ""
                if provider_mode == "responses":
                    rendered.append({"type": "input_text", "text": text_value})
                else:
                    rendered.append({"type": "text", "text": text_value})
                continue
            if part.type == "json":
                rendered.append({"type": "json", "value": part.value})
                continue
            if part.type in {"image", "file"}:
                resolved = self._render_managed_file_part(part, provider_mode)
                if resolved is not None:
                    rendered.append(resolved)
                    continue
                rendered.append(part.to_dict())
                continue
            rendered.append(part.to_dict())
        return rendered

    def _render_managed_file_part(self, part: MessagePart, provider_mode: str) -> dict[str, Any] | None:
        source = part.source or {}
        if source.get("type") != "managed_file":
            return None
        relative_path = source.get("path")
        if not isinstance(relative_path, str) or not relative_path.strip():
            return None
        if self._managed_files_root is None:
            self._logger.warning("managed file root not configured for runtime injection")
            return None
        candidate = Path(relative_path)
        if candidate.is_absolute():
            self._logger.warning("managed file injection rejected absolute path", extra={"path": relative_path})
            return None
        path = (self._managed_files_root / candidate).resolve()
        if not path.is_relative_to(self._managed_files_root):
            self._logger.warning("managed file injection rejected path escape", extra={"path": relative_path})
            return None
        if not path.exists() or not path.is_file():
            self._logger.warning(
                "managed file missing on disk for injection",
                extra={"path": relative_path, "resolved_path": str(path)},
            )
            return None
        payload = path.read_bytes()
        encoded = base64.b64encode(payload).decode("ascii")
        mime = part.mime or "application/octet-stream"
        data_url = f"data:{mime};base64,{encoded}"
        self._logger.debug(
            "rendered managed file for provider payload",
            extra={
                "path": relative_path,
                "resolved_path": str(path),
                "mime": mime,
                "size": len(payload),
                "provider_mode": provider_mode,
                "part_type": part.type,
            },
        )

        if provider_mode == "responses":
            if part.type == "image":
                return {"type": "input_image", "image_url": data_url}
            return {
                "type": "input_file",
                "filename": part.filename or path.name,
                "file_data": data_url,
            }

        if provider_mode == "chat_completions":
            if part.type == "image":
                return {"type": "image_url", "image_url": {"url": data_url}}
            return {
                "type": "file",
                "file": {
                    "filename": part.filename or path.name,
                    "file_data": data_url,
                },
            }

        return None

    @staticmethod
    def _stringify_parts(parts: Sequence[MessagePart]) -> str:
        if len(parts) == 1 and parts[0].type == "text" and parts[0].text is not None:
            return parts[0].text
        if len(parts) == 1 and parts[0].type == "json":
            return json.dumps(parts[0].value, ensure_ascii=True, default=str)
        return json.dumps([part.to_dict() for part in parts], ensure_ascii=True, default=str)
