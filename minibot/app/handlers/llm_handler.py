from __future__ import annotations

import logging
import re
from typing import Any, Literal, Sequence
from typing import cast

import ast
import json

from minibot.app.agent_runtime import AgentRuntime
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart, MessageRole, RuntimeLimits
from minibot.core.channels import ChannelMessage, ChannelResponse, IncomingFileRef, RenderableResponse
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient
from minibot.shared.prompt_loader import load_channel_prompt, load_policy_prompts
from minibot.shared.utils import session_id_for, session_id_from_parts
from minibot.llm.tools.base import ToolBinding, ToolContext


class LLMMessageHandler:
    def __init__(
        self,
        memory: MemoryBackend,
        llm_client: LLMClient,
        tools: Sequence[ToolBinding] | None = None,
        default_owner_id: str | None = None,
        max_history_messages: int | None = None,
        agent_timeout_seconds: int = 120,
    ) -> None:
        self._memory = memory
        self._llm_client = llm_client
        self._tools = list(tools or [])
        self._default_owner_id = default_owner_id
        self._max_history_messages = max_history_messages
        self._prompts_dir = self._llm_prompts_dir()
        max_tool_iterations_getter = getattr(self._llm_client, "max_tool_iterations", None)
        max_tool_iterations = 8
        if callable(max_tool_iterations_getter):
            maybe_value = max_tool_iterations_getter()
            if isinstance(maybe_value, int) and maybe_value > 0:
                max_tool_iterations = maybe_value
        runtime_limits = RuntimeLimits(
            max_steps=max_tool_iterations,
            max_tool_calls=max(12, max_tool_iterations * 2),
            timeout_seconds=max(120, int(agent_timeout_seconds)),
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
                "use file-management tools instead. If the user only uploaded files and gave no clear instruction, "
                "ask a clarifying question."
            )
        return "\n\n".join(fragments)

    async def handle(self, event: MessageEvent) -> ChannelResponse:
        message = event.message
        session_id = session_id_for(message)
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
        await self._memory.append_history(session_id, "user", self._build_history_user_entry(message, model_text))
        await self._enforce_history_limit(session_id)

        if message.attachments and not self._supports_media_inputs():
            answer = "Media inputs are supported for `openai_responses`, `openai`, and `openrouter`."
            await self._memory.append_history(session_id, "assistant", answer)
            await self._enforce_history_limit(session_id)
            chat_id = message.chat_id or message.user_id or 0
            return ChannelResponse(
                channel=message.channel,
                chat_id=chat_id,
                text=answer,
                render=self._plain_render(answer),
                metadata=self._response_metadata(True),
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
        tool_required_intent = False
        suggested_tool: str | None = None
        suggested_path: str | None = None
        tool_messages_count: int | None = None
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
                render, should_reply = self._extract_answer(generation.payload)
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
                tool_messages_count = self._count_tool_messages_in_state(generation.state)
                if tool_messages_count == 0:
                    tool_required_intent, suggested_tool, suggested_path = await self._decide_tool_requirement(
                        history=history,
                        user_text=model_text,
                        prompt_cache_key=prompt_cache_key,
                    )
                if tool_required_intent and tool_messages_count == 0:
                    self._logger.debug(
                        "tool-required request returned without tool calls; retrying with stricter instruction",
                        extra={"chat_id": message.chat_id, "channel": message.channel},
                    )
                    retry_system_prompt = (
                        f"{system_prompt}\n\n"
                        "Tool policy reminder: this request requires using tools before final answer. "
                        "Call the relevant tool now, then provide the final answer from tool output. "
                        "Do not answer with intent statements like 'I will check'."
                    )
                    retry_state = self._build_agent_state(
                        history=history,
                        user_text=model_text,
                        user_content=model_user_content,
                        system_prompt=retry_system_prompt,
                    )
                    generation = await self._runtime.run(
                        state=retry_state,
                        tool_context=tool_context,
                        response_schema=self._response_schema(),
                        prompt_cache_key=prompt_cache_key,
                    )
                    tool_messages_count = self._count_tool_messages_in_state(generation.state)
                    self._logger.debug(
                        "tool-required retry completed",
                        extra={
                            "chat_id": message.chat_id,
                            "channel": message.channel,
                            "tool_messages": tool_messages_count,
                        },
                    )
                render, should_reply = self._extract_answer(generation.payload)

            if tool_required_intent and tool_messages_count == 0:
                direct_tool_message = await self._attempt_direct_file_delete(
                    user_text=message.text,
                    tool_context=tool_context,
                    suggested_tool=suggested_tool,
                    suggested_path=suggested_path,
                )
                if direct_tool_message is not None:
                    self._logger.info(
                        "resolved tool-required delete request via direct delete_file fallback",
                        extra={"chat_id": message.chat_id, "channel": message.channel},
                    )
                    render = self._plain_render(direct_tool_message)
                    should_reply = True
                else:
                    self._logger.warning(
                        "tool-required request still unresolved after retries; returning explicit failure message",
                        extra={
                            "chat_id": message.chat_id,
                            "channel": message.channel,
                            "no_tool_call": True,
                            "tool_exception_available": False,
                            "reason": "model_returned_final_without_tool_calls",
                            "user_text": message.text,
                        },
                    )
                    render = self._plain_render(
                        "I could not verify or execute that action with tools in this attempt. "
                        "Please try again, or ask me to run a specific tool."
                    )
                if not should_reply:
                    self._logger.debug(
                        "overriding should_reply=false for tool-required request without tool outputs",
                        extra={"chat_id": message.chat_id, "channel": message.channel},
                    )
                    should_reply = True

            self._logger.debug(
                "structured output parsed",
                extra={
                    "kind": render.kind,
                    "content_length": len(render.text),
                    "should_reply": should_reply,
                },
            )
        except Exception as exc:
            self._logger.exception("LLM call failed", exc_info=exc)
            render = self._plain_render(self._format_runtime_error_message(exc))
            should_reply = True
        answer = render.text
        await self._memory.append_history(session_id, "assistant", answer)
        await self._enforce_history_limit(session_id)

        chat_id = message.chat_id or message.user_id or 0
        return ChannelResponse(
            channel=message.channel,
            chat_id=chat_id,
            text=answer,
            render=render,
            metadata=self._response_metadata(should_reply),
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
        render, _ = self._extract_answer(generation.payload)
        await self._memory.append_history(session_id, "assistant", render.text)
        await self._enforce_history_limit(session_id)
        metadata = self._response_metadata(True)
        metadata["format_repair_attempt"] = attempt
        metadata["format_repair_original_kind"] = original_kind
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
        incoming_files = self._incoming_files_from_metadata(message.metadata)
        if incoming_files and not message.attachments:
            return self._build_incoming_files_text(prompt_text, incoming_files), None
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

    def _build_history_user_entry(self, message: ChannelMessage, model_text: str) -> str:
        base_text = message.text.strip() if message.text else ""
        attachment_summary = self._summarize_attachments_for_memory(message.attachments)
        incoming_files = self._incoming_files_from_metadata(message.metadata)
        incoming_file_summary = self._summarize_incoming_files_for_memory(incoming_files)
        if not attachment_summary and not incoming_file_summary:
            return base_text
        visible_text = base_text or model_text
        parts = [item for item in [attachment_summary, incoming_file_summary] if item]
        summary = ", ".join(parts)
        if visible_text:
            return f"{visible_text}\nAttachments: {summary}"
        return f"Attachments: {summary}"

    def _summarize_attachments_for_memory(self, attachments: Sequence[dict[str, Any]]) -> str:
        summaries: list[str] = []
        for attachment in attachments:
            attachment_type = attachment.get("type")
            if attachment_type == "input_image":
                summaries.append("image")
                continue
            if attachment_type == "input_file":
                filename = attachment.get("filename")
                if isinstance(filename, str) and filename.strip():
                    summaries.append(f"file:{filename.strip()}")
                else:
                    summaries.append("file")
                continue
            summaries.append("attachment")
        return ", ".join(summaries)

    @staticmethod
    def _incoming_files_from_metadata(metadata: dict[str, Any] | None) -> list[IncomingFileRef]:
        if not isinstance(metadata, dict):
            return []
        raw = metadata.get("incoming_files")
        if not isinstance(raw, list):
            return []
        parsed: list[IncomingFileRef] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                parsed.append(IncomingFileRef.model_validate(item))
            except Exception:
                continue
        return parsed

    def _build_incoming_files_text(self, prompt_text: str, incoming_files: Sequence[IncomingFileRef]) -> str:
        intent = self._infer_incoming_file_intent(prompt_text)
        lines = [
            "Incoming managed files:",
            *[
                (
                    f"- {item.filename} (path={item.path}, mime={item.mime}, size={item.size_bytes} bytes"
                    f", source={item.source}, caption={item.caption or ''})"
                )
                for item in incoming_files
            ],
        ]
        first_path = incoming_files[0].path if incoming_files else ""
        suggested_destination = self._suggest_persist_destination(first_path)
        if intent == "management":
            lines.append(
                "Intent looks like file management. Prefer move_file/delete_file/send_file/list_files. "
                "Do NOT call self_insert_artifact unless user explicitly asks to inspect content."
            )
            if suggested_destination:
                lines.append(
                    "If user asked to save, move_file "
                    f"source_path={first_path} destination_path={suggested_destination}."
                )
        elif intent == "analysis":
            lines.append(
                "Intent looks like content inspection. Use self_insert_artifact if needed to analyze file contents."
            )
        else:
            lines.append("If intent is unclear, ask a clarifying question before acting.")
        if prompt_text:
            return f"{prompt_text}\n\n" + "\n".join(lines)
        return (
            "The user uploaded file(s) but did not include a clear instruction.\n"
            + "\n".join(lines)
            + "\nAsk the user what to do, unless the intent is already obvious."
        )

    @staticmethod
    def _infer_incoming_file_intent(prompt_text: str) -> str:
        normalized = prompt_text.lower().strip()
        if not normalized:
            return "unknown"
        management_keywords = {
            "save",
            "store",
            "keep",
            "move",
            "rename",
            "delete",
            "remove",
            "send",
            "forward",
            "list",
        }
        analysis_keywords = {
            "analyze",
            "analysis",
            "describe",
            "read",
            "what is",
            "summarize",
            "extract",
            "inspect",
            "about",
        }
        if any(keyword in normalized for keyword in management_keywords):
            return "management"
        if any(keyword in normalized for keyword in analysis_keywords):
            return "analysis"
        return "unknown"

    @staticmethod
    def _suggest_persist_destination(path: str) -> str | None:
        marker = "uploads/temp/"
        if path.startswith(marker):
            return f"uploads/{path[len(marker) :]}"
        return None

    @staticmethod
    def _summarize_incoming_files_for_memory(incoming_files: Sequence[IncomingFileRef]) -> str:
        if not incoming_files:
            return ""
        return ", ".join([f"file:{item.filename}" for item in incoming_files])

    def _response_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["text", "html", "markdown_v2"],
                            "description": (
                                "Strictly declare the actual output format you are returning in answer.content. "
                                "If answer.content is HTML, set kind=html. If MarkdownV2, set kind=markdown_v2. "
                                "If plain text, set kind=text. Do not default to text when using formatting."
                                "This kind controls formatting in the channel renderer."
                            ),
                        },
                        "content": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "Main reply body. For html, use Telegram-supported inline tags only; "
                                "do not output full HTML documents with doctype/head/body/style/script/div/p/br. "
                                "Use newline characters instead of <br>."
                            ),
                        },
                        "meta": {
                            "type": "object",
                            "properties": {
                                "disable_link_preview": {"type": "boolean"},
                            },
                            "required": [],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["kind", "content"],
                    "additionalProperties": False,
                },
                "should_answer_to_user": {"type": "boolean"},
            },
            "required": ["answer", "should_answer_to_user"],
            "additionalProperties": False,
        }

    async def _enforce_history_limit(self, session_id: str) -> None:
        if self._max_history_messages is None:
            return
        await self._memory.trim_history(session_id, self._max_history_messages)

    def _extract_answer(self, payload: Any) -> tuple[RenderableResponse, bool]:
        if isinstance(payload, dict):
            answer = payload.get("answer")
            should = payload.get("should_answer_to_user")
            render = self._render_from_payload(answer)
            should_flag = self._coerce_should_answer(should)
            if render is not None and should_flag is not None:
                self._logger.debug(
                    "structured output extracted from dict payload",
                    extra={"kind": render.kind, "has_answer_object": isinstance(answer, dict)},
                )
                return render, should_flag
            if render is not None and should is None:
                self._logger.debug(
                    "structured output missing should_answer_to_user; defaulting to true",
                    extra={"kind": render.kind},
                )
                return render, True
            result = payload.get("result")
            if isinstance(result, str):
                return self._plain_render(result), True
            timestamp = payload.get("timestamp")
            if isinstance(timestamp, str):
                return self._plain_render(timestamp), True
        if isinstance(payload, str):
            parsed: Any | None = None
            try:
                parsed = json.loads(payload)
            except Exception:
                try:
                    parsed = ast.literal_eval(payload)
                except Exception:
                    parsed = None
            if isinstance(parsed, dict):
                answer = parsed.get("answer")
                should = parsed.get("should_answer_to_user")
                render = self._render_from_payload(answer)
                should_flag = self._coerce_should_answer(should)
                if render is not None and should_flag is not None:
                    self._logger.debug(
                        "structured output extracted from parsed string payload",
                        extra={"kind": render.kind, "has_answer_object": isinstance(answer, dict)},
                    )
                    return render, should_flag
                if render is not None and should is None:
                    self._logger.debug(
                        "structured output missing should_answer_to_user in parsed payload; defaulting to true",
                        extra={"kind": render.kind},
                    )
                    return render, True
                result = parsed.get("result")
                if isinstance(result, str):
                    return self._plain_render(result), True
                timestamp = parsed.get("timestamp")
                if isinstance(timestamp, str):
                    return self._plain_render(timestamp), True
                self._logger.debug(
                    "parsed payload looked structured but failed validation",
                    extra={
                        "parsed_keys": sorted(str(key) for key in parsed.keys()),
                        "should_type": type(should).__name__,
                    },
                )
            return self._plain_render(payload), True
        return self._plain_render(str(payload)), True

    @staticmethod
    def _coerce_should_answer(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
        if isinstance(value, int) and value in {0, 1}:
            return bool(value)
        return None

    def _render_from_payload(self, value: Any) -> RenderableResponse | None:
        if isinstance(value, str):
            self._logger.debug("structured output answer is legacy string; forcing text kind")
            return self._plain_render(value)
        if not isinstance(value, dict):
            return None

        content_value = value.get("content")
        if not isinstance(content_value, str):
            legacy_text = value.get("text")
            if isinstance(legacy_text, str):
                content_value = legacy_text

        raw_kind = value.get("kind")
        normalized_kind = self._normalize_render_kind(raw_kind)
        meta_value = value.get("meta")
        normalized_meta = meta_value if isinstance(meta_value, dict) else {}

        if isinstance(content_value, str) and normalized_kind is not None:
            if not isinstance(meta_value, dict) and meta_value is not None:
                self._logger.debug(
                    "structured output meta is not an object; coercing to empty object",
                    extra={"meta_type": type(meta_value).__name__},
                )
            render = RenderableResponse(kind=normalized_kind, text=content_value, meta=normalized_meta)
            self._logger.debug(
                "structured output answer object normalized",
                extra={
                    "kind": render.kind,
                    "meta_keys": sorted(render.meta.keys()),
                    "source_keys": sorted(str(key) for key in value.keys()),
                },
            )
            if not render.text.strip():
                return None
            return render

        try:
            render = RenderableResponse.model_validate(value)
        except Exception as exc:
            text = content_value
            if isinstance(text, str):
                self._logger.debug(
                    "structured output answer object invalid; using plain text fallback",
                    extra={
                        "available_keys": sorted(str(key) for key in value.keys()),
                        "validation_error": str(exc),
                        "raw_kind": raw_kind,
                    },
                )
                return self._plain_render(text)
            return None
        if not render.text.strip():
            return None
        self._logger.debug(
            "structured output answer object validated",
            extra={
                "kind": render.kind,
                "meta_keys": sorted(render.meta.keys()),
            },
        )
        return render

    @staticmethod
    def _normalize_render_kind(value: Any) -> Literal["text", "html", "markdown_v2"] | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        if normalized in {"text", "plain", "plain_text", "plaintext"}:
            return "text"
        if normalized in {"html", "htm"}:
            return "html"
        if normalized in {"markdown_v2", "markdownv2", "markdown", "md"}:
            return "markdown_v2"
        return None

    @staticmethod
    def _plain_render(text: str) -> RenderableResponse:
        return RenderableResponse(kind="text", text=text)

    async def _decide_tool_requirement(
        self,
        *,
        history: Sequence[Any],
        user_text: str,
        prompt_cache_key: str | None,
    ) -> tuple[bool, str | None, str | None]:
        if not self._tools:
            return False, None, None
        tool_names = [binding.tool.name for binding in self._tools]
        classifier_prompt = (
            "Decide whether the user's request requires executing at least one tool before answering. "
            "Use the available tool names exactly as given. "
            "Return structured output only.\n\n"
            f"Available tools: {', '.join(tool_names)}\n"
            f"User request:\n{user_text}"
        )
        try:
            generation = await self._llm_client.generate(
                history,
                classifier_prompt,
                user_content=None,
                tools=[],
                tool_context=None,
                response_schema=self._tool_requirement_schema(),
                prompt_cache_key=f"{prompt_cache_key}:tool-requirement" if prompt_cache_key else None,
                previous_response_id=None,
                system_prompt_override="You are a strict tool-routing classifier.",
            )
            payload = generation.payload
            payload_obj: dict[str, Any] | None = None
            if isinstance(payload, dict):
                payload_obj = payload
            elif isinstance(payload, str):
                stripped = payload.strip()
                if stripped:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        payload_obj = parsed
            if not payload_obj:
                return False, None, None
            raw_required = payload_obj.get("requires_tools", False)
            requires_tools = bool(raw_required)
            raw_tool = payload_obj.get("suggested_tool")
            suggested_tool = raw_tool if isinstance(raw_tool, str) and raw_tool in tool_names else None
            raw_path = payload_obj.get("path")
            suggested_path = raw_path.strip() if isinstance(raw_path, str) and raw_path.strip() else None
            self._logger.debug(
                "tool requirement decision computed",
                extra={
                    "requires_tools": requires_tools,
                    "suggested_tool": suggested_tool,
                    "suggested_path": suggested_path,
                },
            )
            return requires_tools, suggested_tool, suggested_path
        except Exception:
            self._logger.exception("tool requirement decision failed")
            return False, None, None

    @staticmethod
    def _tool_requirement_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "requires_tools": {"type": "boolean"},
                "suggested_tool": {"type": ["string", "null"]},
                "path": {"type": ["string", "null"]},
            },
            "required": ["requires_tools", "suggested_tool", "path"],
            "additionalProperties": False,
        }

    @staticmethod
    def _count_tool_messages_in_state(state: AgentState) -> int:
        return sum(1 for message in state.messages if message.role == "tool")

    async def _attempt_direct_file_delete(
        self,
        *,
        user_text: str,
        tool_context: ToolContext,
        suggested_tool: str | None,
        suggested_path: str | None,
    ) -> str | None:
        if suggested_tool != "delete_file":
            return None
        binding = next((item for item in self._tools if item.tool.name == "delete_file"), None)
        if binding is None:
            return None
        candidates = self._extract_delete_path_candidates(user_text)
        if suggested_path is not None and suggested_path not in candidates:
            candidates.insert(0, suggested_path)
        if not candidates:
            return None
        self._logger.debug(
            "attempting direct delete_file fallback",
            extra={"candidate_count": len(candidates), "candidates": candidates[:5]},
        )
        last_message: str | None = None
        for candidate in candidates:
            try:
                payload = {"path": candidate}
                raw_result = await binding.handler(payload, tool_context)
                if not isinstance(raw_result, dict):
                    continue
                message = str(raw_result.get("message") or "")
                deleted_count = int(raw_result.get("deleted_count") or 0)
                if deleted_count > 0:
                    return message or f"Deleted file successfully: {candidate}"
                if message:
                    last_message = message
            except Exception:
                self._logger.exception("direct delete_file fallback failed", extra={"path": candidate})
        return last_message

    @staticmethod
    def _extract_delete_path_candidates(text: str) -> list[str]:
        candidates: list[str] = []

        def _normalize(path_value: str) -> str | None:
            value = path_value.strip().strip('"').strip("'")
            if not value:
                return None
            if value.startswith("./"):
                value = value[2:]
            return value.replace("\\", "/")

        quoted = re.findall(r"['\"]([^'\"]+)['\"]", text)
        for item in quoted:
            normalized = _normalize(item)
            if normalized and normalized not in candidates:
                candidates.append(normalized)

        inline_paths = re.findall(r"(?:\.?/?[a-zA-Z0-9_-]+/)+[a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,10}", text)
        for item in inline_paths:
            normalized = _normalize(item)
            if normalized and normalized not in candidates:
                candidates.append(normalized)

        filename_match = re.search(r"([a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,10})", text)
        if filename_match:
            filename = _normalize(filename_match.group(1))
            if filename and filename not in candidates:
                candidates.append(filename)

        return candidates

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
