from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Sequence
from typing import cast

from minibot.app.agent_runtime import AgentRuntime
from minibot.app.delegation_trace import count_tool_messages, extract_delegation_trace
from minibot.app.incoming_files_context import (
    build_history_user_entry,
    build_incoming_files_text,
    incoming_files_from_metadata,
)
from minibot.app.runtime_limits import build_runtime_limits
from minibot.app.response_parser import extract_answer, plain_render
from minibot.app.tool_use_guardrail import NoopToolUseGuardrail, ToolUseGuardrail
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart, MessageRole
from minibot.core.channels import ChannelMessage, ChannelResponse
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient
from minibot.shared.assistant_response import assistant_response_schema
from minibot.shared.prompt_loader import load_channel_prompt, load_compact_prompt, load_policy_prompts
from minibot.shared.utils import session_id_for, session_id_from_parts
from minibot.llm.tools.base import ToolBinding, ToolContext


@dataclass(frozen=True)
class _CompactionResult:
    updates: list[str]
    performed: bool
    tokens_used: int
    session_total_tokens_before_compaction: int | None
    session_total_tokens_after_compaction: int


class LLMMessageHandler:
    _COMPACTION_USER_REQUEST = "Please compact the current conversation memory."

    def __init__(
        self,
        memory: MemoryBackend,
        llm_client: LLMClient,
        tools: Sequence[ToolBinding] | None = None,
        default_owner_id: str | None = None,
        max_history_messages: int | None = None,
        max_history_tokens: int | None = None,
        notify_compaction_updates: bool = False,
        agent_timeout_seconds: int = 120,
        environment_prompt_fragment: str = "",
        tool_use_guardrail: ToolUseGuardrail | None = None,
    ) -> None:
        self._memory = memory
        self._llm_client = llm_client
        self._tools = list(tools or [])
        self._default_owner_id = default_owner_id
        self._max_history_messages = max_history_messages
        self._max_history_tokens = max_history_tokens
        self._notify_compaction_updates = notify_compaction_updates
        self._tool_use_guardrail: ToolUseGuardrail = tool_use_guardrail or NoopToolUseGuardrail()
        self._session_total_tokens: dict[str, int] = {}
        self._prompts_dir = self._llm_prompts_dir()
        self._environment_prompt_fragment = environment_prompt_fragment.strip()
        runtime_limits = build_runtime_limits(
            llm_client=self._llm_client,
            timeout_seconds=agent_timeout_seconds,
            min_timeout_seconds=120,
        )
        managed_files_root = self._managed_files_root_from_tools()
        if self._supports_agent_runtime():
            self._runtime = AgentRuntime(
                llm_client=self._llm_client,
                tools=self._tools,
                limits=runtime_limits,
                allowed_append_message_tools=["self_insert_artifact"],
                allow_system_inserts=False,
                managed_files_root=managed_files_root,
            )
        else:
            self._runtime = None
        self._logger = logging.getLogger("minibot.handler")

    def _supports_agent_runtime(self) -> bool:
        return callable(getattr(self._llm_client, "complete_once", None)) and callable(
            getattr(self._llm_client, "execute_tool_calls_for_runtime", None)
        )

    def _managed_files_root_from_tools(self) -> str | None:
        for binding in self._tools:
            if binding.tool.name != "self_insert_artifact":
                continue
            owner = getattr(binding.handler, "__self__", None)
            storage = getattr(owner, "_storage", None)
            root = getattr(storage, "root_dir", None)
            if root is not None:
                return str(root)
        return None

    def _llm_provider_name(self) -> str | None:
        provider_getter = getattr(self._llm_client, "provider_name", None)
        if callable(provider_getter):
            provider = provider_getter()
            if isinstance(provider, str) and provider:
                return provider
        return None

    def _llm_model_name(self) -> str | None:
        model_getter = getattr(self._llm_client, "model_name", None)
        if callable(model_getter):
            model = model_getter()
            if isinstance(model, str) and model:
                return model
        return None

    def _response_metadata(self, should_reply: bool) -> dict[str, Any]:
        return {
            "should_reply": should_reply,
            "llm_provider": self._llm_provider_name(),
            "llm_model": self._llm_model_name(),
        }

    def _supports_media_inputs(self) -> bool:
        supports_getter = getattr(self._llm_client, "supports_media_inputs", None)
        if callable(supports_getter):
            return bool(supports_getter())
        return self._llm_client.is_responses_provider()

    def _media_input_mode(self) -> str:
        mode_getter = getattr(self._llm_client, "media_input_mode", None)
        if callable(mode_getter):
            mode = mode_getter()
            if isinstance(mode, str) and mode:
                return mode
        return "responses" if self._llm_client.is_responses_provider() else "none"

    def _llm_prompts_dir(self) -> str:
        prompts_dir_getter = getattr(self._llm_client, "prompts_dir", None)
        if callable(prompts_dir_getter):
            value = prompts_dir_getter()
            if isinstance(value, str) and value:
                return value
        return "./prompts"

    def _compose_system_prompt(self, channel: str | None) -> str:
        system_prompt_getter = getattr(self._llm_client, "system_prompt", None)
        base_prompt = "You are Minibot, a helpful assistant."
        if callable(system_prompt_getter):
            maybe_prompt = system_prompt_getter()
            if isinstance(maybe_prompt, str) and maybe_prompt:
                base_prompt = maybe_prompt

        fragments = [base_prompt]
        fragments.extend(load_policy_prompts(self._prompts_dir))
        channel_prompt = load_channel_prompt(self._prompts_dir, channel)
        if channel_prompt:
            fragments.append(channel_prompt)
        if self._environment_prompt_fragment:
            fragments.append(self._environment_prompt_fragment)
        self._logger.debug(
            "composed system prompt",
            extra={
                "channel": channel,
                "prompts_dir": self._prompts_dir,
                "channel_prompt_loaded": bool(channel_prompt),
                "fragment_count": len(fragments),
                "prompt_preview": "\n\n".join(fragments)[:200],
            },
        )

        if any(binding.tool.name == "self_insert_artifact" for binding in self._tools):
            fragments.append(
                "When you need to inspect a local workspace file (image/document), call self_insert_artifact first "
                "to inject it into conversation context before answering file contents. "
                "For file-management requests (save, move, delete, send, list), do not call self_insert_artifact; "
                "use the filesystem tool with the appropriate action instead. "
                "If the user only uploaded files and gave no clear instruction, ask a clarifying question."
            )
        return "\n\n".join(fragments)

    async def handle(self, event: MessageEvent) -> ChannelResponse:
        message = event.message
        session_id = session_id_for(message)
        turn_total_tokens = 0
        model_text, model_user_content = self._build_model_user_input(message)
        if message.attachments:
            self._logger.debug(
                "prepared multimodal message",
                extra={
                    "channel": message.channel,
                    "chat_id": message.chat_id,
                    "user_id": message.user_id,
                    "attachment_count": len(message.attachments),
                    "attachment_types": [str(attachment.get("type", "unknown")) for attachment in message.attachments],
                    "responses_provider": self._llm_client.is_responses_provider(),
                    "media_input_mode": self._media_input_mode(),
                },
            )
        await self._memory.append_history(session_id, "user", build_history_user_entry(message, model_text))
        await self._enforce_history_limit(session_id)

        if message.attachments and not self._supports_media_inputs():
            answer = "Media inputs are supported for `openai_responses`, `openai`, and `openrouter`."
            await self._memory.append_history(session_id, "assistant", answer)
            await self._enforce_history_limit(session_id)
            chat_id = message.chat_id or message.user_id or 0
            metadata = self._response_metadata(True)
            metadata["token_trace"] = self._build_token_trace(
                turn_total_tokens=0,
                session_total_tokens_before_compaction=None,
                session_total_tokens_after_compaction=self._current_session_tokens(session_id),
                compaction_performed=False,
            )
            return ChannelResponse(
                channel=message.channel,
                chat_id=chat_id,
                text=answer,
                render=plain_render(answer),
                metadata=metadata,
            )

        history = list(await self._memory.get_history(session_id))
        owner_id = resolve_owner_id(message, self._default_owner_id)
        tool_context = ToolContext(
            owner_id=owner_id,
            channel=message.channel,
            chat_id=message.chat_id,
            user_id=message.user_id,
        )
        prompt_cache_key = _prompt_cache_key(message)
        system_prompt = self._compose_system_prompt(message.channel)
        primary_agent = "minibot"
        agent_trace: list[dict[str, Any]] = []
        delegation_fallback_used = False
        try:
            if self._runtime is None:
                generation = await self._llm_client.generate(
                    history,
                    model_text,
                    user_content=model_user_content,
                    tools=self._tools,
                    tool_context=tool_context,
                    response_schema=self._response_schema(),
                    prompt_cache_key=prompt_cache_key,
                    previous_response_id=None,
                    system_prompt_override=system_prompt,
                )
                turn_total_tokens += self._track_token_usage(session_id, getattr(generation, "total_tokens", None))
                render, should_reply = extract_answer(generation.payload, logger=self._logger)
            else:
                state = self._build_agent_state(
                    history=history,
                    user_text=model_text,
                    user_content=model_user_content,
                    system_prompt=system_prompt,
                )
                generation = await self._runtime.run(
                    state=state,
                    tool_context=tool_context,
                    response_schema=self._response_schema(),
                    prompt_cache_key=prompt_cache_key,
                )
                turn_total_tokens += self._track_token_usage(session_id, getattr(generation, "total_tokens", None))
                tool_messages_count = count_tool_messages(generation.state)
                trace_result = extract_delegation_trace(generation.state)
                agent_trace = trace_result.trace
                delegation_fallback_used = trace_result.fallback_used
                delegation_unresolved = trace_result.unresolved

                guardrail = await self._tool_use_guardrail.apply(
                    session_id=session_id,
                    user_text=model_text,
                    tool_context=tool_context,
                    state=generation.state,
                    system_prompt=system_prompt,
                    prompt_cache_key=prompt_cache_key,
                )
                turn_total_tokens += guardrail.tokens_used
                if guardrail.resolved_render_text is not None:
                    render = plain_render(guardrail.resolved_render_text)
                    should_reply = True
                elif guardrail.requires_retry and tool_messages_count == 0:
                    retry_state = self._build_agent_state(
                        history=history,
                        user_text=model_text,
                        user_content=model_user_content,
                        system_prompt=f"{system_prompt}\n\n{guardrail.retry_system_prompt_suffix}",
                    )
                    generation = await self._runtime.run(
                        state=retry_state,
                        tool_context=tool_context,
                        response_schema=self._response_schema(),
                        prompt_cache_key=prompt_cache_key,
                    )
                    turn_total_tokens += self._track_token_usage(session_id, getattr(generation, "total_tokens", None))
                    trace_result = extract_delegation_trace(generation.state)
                    agent_trace = trace_result.trace
                    delegation_fallback_used = trace_result.fallback_used
                    delegation_unresolved = trace_result.unresolved
                    render, should_reply = extract_answer(generation.payload, logger=self._logger)
                    if count_tool_messages(generation.state) == 0:
                        render = plain_render(
                            "I could not verify or execute that action with tools in this attempt. "
                            "Please try again, or ask me to run a specific tool."
                        )
                        should_reply = True
                else:
                    render, should_reply = extract_answer(generation.payload, logger=self._logger)
                    if delegation_unresolved:
                        retry_state = self._build_agent_state(
                            history=history,
                            user_text=model_text,
                            user_content=model_user_content,
                            system_prompt=(
                                f"{system_prompt}\n\n"
                                "Delegation policy reminder: If invoke_agent result has should_answer_to_user=false "
                                "or result_status other than success, you must resolve it in this turn. "
                                "Do one additional concrete tool call or return an explicit failure message to user "
                                "with should_answer_to_user=true."
                            ),
                        )
                        generation = await self._runtime.run(
                            state=retry_state,
                            tool_context=tool_context,
                            response_schema=self._response_schema(),
                            prompt_cache_key=prompt_cache_key,
                        )
                        turn_total_tokens += self._track_token_usage(
                            session_id, getattr(generation, "total_tokens", None)
                        )
                        trace_result = extract_delegation_trace(generation.state)
                        agent_trace = trace_result.trace
                        delegation_fallback_used = trace_result.fallback_used
                        delegation_unresolved = trace_result.unresolved
                        render, should_reply = extract_answer(generation.payload, logger=self._logger)
                        if delegation_unresolved:
                            self._logger.warning(
                                "delegation unresolved after bounded retry; returning explicit failure",
                                extra={"chat_id": message.chat_id, "channel": message.channel},
                            )
                            render = plain_render(
                                "I could not complete that delegated action reliably in this attempt. "
                                "Please retry, or ask me to run a specific tool step-by-step."
                            )
                            should_reply = True

            self._logger.debug(
                "structured output parsed",
                extra={"kind": render.kind, "content_length": len(render.text), "should_reply": should_reply},
            )
        except Exception as exc:
            self._logger.exception("LLM call failed", exc_info=exc)
            render = plain_render(self._format_runtime_error_message(exc))
            should_reply = True
        answer = render.text
        await self._memory.append_history(session_id, "assistant", answer)
        await self._enforce_history_limit(session_id)
        compact_prompt_cache_key = prompt_cache_key or f"{session_id}:runtime"
        compaction_result = await self._compact_history_if_needed(
            session_id,
            prompt_cache_key=compact_prompt_cache_key,
            system_prompt=system_prompt,
            notify=self._notify_compaction_updates,
        )
        turn_total_tokens += compaction_result.tokens_used

        chat_id = message.chat_id or message.user_id or 0
        metadata = self._response_metadata(should_reply)
        metadata["primary_agent"] = primary_agent
        if agent_trace:
            metadata["agent_trace"] = agent_trace
        metadata["delegation_fallback_used"] = delegation_fallback_used
        if compaction_result.updates:
            metadata["compaction_updates"] = compaction_result.updates
        metadata["token_trace"] = self._build_token_trace(
            turn_total_tokens=turn_total_tokens,
            session_total_tokens_before_compaction=compaction_result.session_total_tokens_before_compaction,
            session_total_tokens_after_compaction=compaction_result.session_total_tokens_after_compaction,
            compaction_performed=compaction_result.performed,
        )
        return ChannelResponse(
            channel=message.channel,
            chat_id=chat_id,
            text=answer,
            render=render,
            metadata=metadata,
        )

    async def repair_format_response(
        self,
        *,
        response: ChannelResponse,
        parse_error: str,
        channel: str,
        chat_id: int,
        user_id: int | None,
        attempt: int,
    ) -> ChannelResponse:
        session_id = session_id_from_parts(channel, chat_id, user_id)
        turn_total_tokens = 0
        history = list(await self._memory.get_history(session_id))
        system_prompt = self._compose_system_prompt(channel)
        original_kind = response.render.kind if response.render is not None else "text"
        original_content = response.render.text if response.render is not None else response.text
        repair_prompt = self._build_format_repair_prompt(
            channel=channel,
            original_kind=original_kind,
            parse_error=parse_error,
            original_content=original_content,
        )
        await self._memory.append_history(session_id, "user", repair_prompt)
        await self._enforce_history_limit(session_id)
        generation = await self._llm_client.generate(
            history,
            repair_prompt,
            user_content=None,
            tools=[],
            tool_context=None,
            response_schema=self._response_schema(),
            prompt_cache_key=f"{channel}:{chat_id}:format-repair",
            previous_response_id=None,
            system_prompt_override=system_prompt,
        )
        turn_total_tokens += self._track_token_usage(session_id, getattr(generation, "total_tokens", None))
        render, _ = extract_answer(generation.payload, logger=self._logger)
        await self._memory.append_history(session_id, "assistant", render.text)
        await self._enforce_history_limit(session_id)
        compaction_result = await self._compact_history_if_needed(
            session_id,
            prompt_cache_key=f"{channel}:{chat_id}:format-repair",
            system_prompt=system_prompt,
            notify=False,
        )
        turn_total_tokens += compaction_result.tokens_used
        metadata = self._response_metadata(True)
        metadata["format_repair_attempt"] = attempt
        metadata["format_repair_original_kind"] = original_kind
        metadata["token_trace"] = self._build_token_trace(
            turn_total_tokens=turn_total_tokens,
            session_total_tokens_before_compaction=compaction_result.session_total_tokens_before_compaction,
            session_total_tokens_after_compaction=compaction_result.session_total_tokens_after_compaction,
            compaction_performed=compaction_result.performed,
        )
        return ChannelResponse(
            channel=channel,
            chat_id=chat_id,
            text=render.text,
            render=render,
            metadata=metadata,
        )

    @staticmethod
    def _build_format_repair_prompt(
        *, channel: str, original_kind: str, parse_error: str, original_content: str
    ) -> str:
        if channel == "telegram":
            return (
                "We tried to send a formatted response to Telegram but got a formatting parse error. "
                "Rewrite the same answer with valid Telegram-compatible formatting.\n\n"
                f"Original kind: {original_kind}\n"
                f"Telegram error: {parse_error}\n\n"
                "Requirements:\n"
                "- Return the same meaning and content, only fix formatting.\n"
                "- Keep kind as markdown_v2 or html only if valid for Telegram, otherwise use text.\n"
                "- For markdown_v2, write normal Markdown (do not pre-escape Telegram MarkdownV2).\n"
                "- Do not use placeholder statements.\n"
                "- Return structured output only.\n\n"
                f"Original content:\n{original_content}"
            )
        return (
            "We tried to send a formatted response to the target channel and got a formatting parse error. "
            "Rewrite the same answer with valid channel-compatible formatting.\n\n"
            f"Channel: {channel}\n"
            f"Original kind: {original_kind}\n"
            f"Parse error: {parse_error}\n\n"
            "Requirements:\n"
            "- Return the same meaning and content, only fix formatting.\n"
            "- Keep kind aligned with valid formatting for this channel, otherwise use text.\n"
            "- Do not use placeholder statements.\n"
            "- Return structured output only.\n\n"
            f"Original content:\n{original_content}"
        )

    def _build_model_user_input(self, message: ChannelMessage) -> tuple[str, str | list[dict[str, Any]] | None]:
        prompt_text = message.text.strip() if message.text else ""
        incoming_files = incoming_files_from_metadata(message.metadata)
        if incoming_files and not message.attachments:
            return build_incoming_files_text(prompt_text, incoming_files), None
        if not message.attachments:
            return prompt_text, None

        resolved_prompt = prompt_text or "Please analyze the attached media and summarize the key information."
        mode = self._media_input_mode()
        parts: list[dict[str, Any]] = []
        if mode == "chat_completions":
            parts.append({"type": "text", "text": resolved_prompt})
        else:
            parts.append({"type": "input_text", "text": resolved_prompt})
        parts.extend(self._transform_attachments_for_mode(message.attachments, mode))
        return resolved_prompt, parts

    def _build_agent_state(
        self,
        history: Sequence[Any],
        user_text: str,
        user_content: str | list[dict[str, Any]] | None,
        system_prompt: str,
    ) -> AgentState:
        messages: list[AgentMessage] = [
            AgentMessage(role="system", content=[MessagePart(type="text", text=system_prompt)])
        ]
        for entry in history:
            role = str(getattr(entry, "role", "user"))
            if role not in {"system", "user", "assistant", "tool"}:
                continue
            content = getattr(entry, "content", "")
            messages.append(
                AgentMessage(
                    role=cast(MessageRole, role),
                    content=[MessagePart(type="text", text=str(content))],
                )
            )

        if user_content is None:
            messages.append(AgentMessage(role="user", content=[MessagePart(type="text", text=user_text)]))
        elif isinstance(user_content, str):
            messages.append(AgentMessage(role="user", content=[MessagePart(type="text", text=user_content)]))
        else:
            messages.append(
                AgentMessage(
                    role="user",
                    content=[MessagePart(type="text", text=user_text)],
                    raw_content=user_content,
                )
            )
        return AgentState(messages=messages)

    def _transform_attachments_for_mode(
        self,
        attachments: Sequence[dict[str, Any]],
        mode: str,
    ) -> list[dict[str, Any]]:
        if mode == "chat_completions":
            return [self._to_chat_completions_attachment(attachment) for attachment in attachments]
        return [dict(attachment) for attachment in attachments]

    @staticmethod
    def _to_chat_completions_attachment(attachment: dict[str, Any]) -> dict[str, Any]:
        attachment_type = attachment.get("type")
        if attachment_type == "input_image":
            image_url = attachment.get("image_url")
            return {
                "type": "image_url",
                "image_url": {
                    "url": image_url,
                },
            }
        if attachment_type == "input_file":
            return {
                "type": "file",
                "file": {
                    "filename": attachment.get("filename"),
                    "file_data": attachment.get("file_data"),
                },
            }
        return dict(attachment)

    def _response_schema(self) -> dict[str, Any]:
        return assistant_response_schema(kinds=["text", "html", "markdown_v2"], include_meta=True)

    async def _enforce_history_limit(self, session_id: str) -> None:
        if self._max_history_messages is None:
            return
        await self._memory.trim_history(session_id, self._max_history_messages)

    def _track_token_usage(self, session_id: str, tokens: int | None) -> int:
        if tokens is None or tokens <= 0:
            return 0
        self._session_total_tokens[session_id] = self._session_total_tokens.get(session_id, 0) + tokens
        return tokens

    def _current_session_tokens(self, session_id: str) -> int:
        return self._session_total_tokens.get(session_id, 0)

    @staticmethod
    def _build_token_trace(
        *,
        turn_total_tokens: int,
        session_total_tokens_before_compaction: int | None,
        session_total_tokens_after_compaction: int,
        compaction_performed: bool,
    ) -> dict[str, Any]:
        return {
            "turn_total_tokens": max(0, int(turn_total_tokens)),
            "session_total_tokens": max(0, int(session_total_tokens_after_compaction)),
            "session_total_tokens_before_compaction": session_total_tokens_before_compaction,
            "session_total_tokens_after_compaction": max(0, int(session_total_tokens_after_compaction)),
            "compaction_performed": compaction_performed,
            "accounting_scope": "all_turn_calls",
        }

    def _compact_system_prompt(self, system_prompt: str) -> str:
        compact_prompt = load_compact_prompt(self._prompts_dir)
        if compact_prompt:
            return f"{system_prompt}\n\n{compact_prompt}"
        return (
            f"{system_prompt}\n\n"
            "You are compacting conversation memory. Return a concise but complete summary of the "
            "conversation so far, preserving user goals, constraints, and pending tasks. "
            "Do not include preamble."
        )

    async def _compact_history_if_needed(
        self,
        session_id: str,
        *,
        prompt_cache_key: str,
        system_prompt: str,
        notify: bool,
    ) -> _CompactionResult:
        updates: list[str] = []
        if self._max_history_tokens is None:
            return _CompactionResult(
                updates=updates,
                performed=False,
                tokens_used=0,
                session_total_tokens_before_compaction=None,
                session_total_tokens_after_compaction=self._current_session_tokens(session_id),
            )
        total_tokens = self._session_total_tokens.get(session_id, 0)
        if total_tokens < self._max_history_tokens:
            return _CompactionResult(
                updates=updates,
                performed=False,
                tokens_used=0,
                session_total_tokens_before_compaction=None,
                session_total_tokens_after_compaction=total_tokens,
            )
        history = list(await self._memory.get_history(session_id))
        if not history:
            session_before_reset = total_tokens
            self._session_total_tokens[session_id] = 0
            return _CompactionResult(
                updates=updates,
                performed=False,
                tokens_used=0,
                session_total_tokens_before_compaction=session_before_reset,
                session_total_tokens_after_compaction=0,
            )
        if notify:
            updates.append("running compaction...")
        compaction_tokens = 0
        compaction_user_request = self._COMPACTION_USER_REQUEST
        try:
            compact_generation = await self._llm_client.generate(
                history,
                compaction_user_request,
                user_content=None,
                tools=[],
                tool_context=None,
                response_schema=None,
                prompt_cache_key=f"{prompt_cache_key}:compact",
                previous_response_id=None,
                system_prompt_override=self._compact_system_prompt(system_prompt),
            )
            compaction_tokens = self._track_token_usage(session_id, getattr(compact_generation, "total_tokens", None))
            session_before_reset = self._current_session_tokens(session_id)
            compact_render, _ = extract_answer(compact_generation.payload, logger=self._logger)
            await self._memory.trim_history(session_id, 0)
            await self._memory.append_history(session_id, "user", compaction_user_request)
            await self._memory.append_history(session_id, "assistant", compact_render.text)
            self._session_total_tokens[session_id] = 0
            if notify:
                updates.append("done compacting")
                updates.append(compact_render.text)
            return _CompactionResult(
                updates=updates,
                performed=True,
                tokens_used=compaction_tokens,
                session_total_tokens_before_compaction=session_before_reset,
                session_total_tokens_after_compaction=0,
            )
        except Exception as exc:
            self._logger.exception("history compaction failed", exc_info=exc)
            if notify:
                updates.append("error compacting")
            return _CompactionResult(
                updates=updates,
                performed=False,
                tokens_used=compaction_tokens,
                session_total_tokens_before_compaction=None,
                session_total_tokens_after_compaction=self._current_session_tokens(session_id),
            )

    def _format_runtime_error_message(self, exc: Exception) -> str:
        if not self._logger.isEnabledFor(logging.DEBUG):
            return "Sorry, I couldn't answer right now."
        error_name = type(exc).__name__
        detail = str(exc).strip().replace("\n", " ")
        if detail:
            if len(detail) > 200:
                detail = f"{detail[:200]}..."
            return f"LLM error ({error_name}): {detail}"
        return f"LLM error ({error_name})."


def resolve_owner_id(message: ChannelMessage, default_owner_id: str | None) -> str:
    if default_owner_id:
        return default_owner_id
    if message.user_id is not None:
        return str(message.user_id)
    if message.chat_id is not None:
        return str(message.chat_id)
    return session_id_for(message)


def _prompt_cache_key(message: ChannelMessage) -> str | None:
    if message.channel and (message.user_id is not None or message.chat_id is not None):
        suffix = message.user_id if message.user_id is not None else message.chat_id
        return f"{message.channel}:{suffix}"
    return None
