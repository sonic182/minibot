from __future__ import annotations

import logging
from typing import Any, Sequence
from typing import cast

import ast
import json

from minibot.app.agent_runtime import AgentRuntime
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart, MessageRole, RuntimeLimits
from minibot.core.channels import ChannelMessage, ChannelResponse
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient
from minibot.shared.utils import session_id_for
from minibot.llm.tools.base import ToolBinding, ToolContext


class LLMMessageHandler:
    def __init__(
        self,
        memory: MemoryBackend,
        llm_client: LLMClient,
        tools: Sequence[ToolBinding] | None = None,
        default_owner_id: str | None = None,
        max_history_messages: int | None = None,
    ) -> None:
        self._memory = memory
        self._llm_client = llm_client
        self._tools = list(tools or [])
        self._default_owner_id = default_owner_id
        self._max_history_messages = max_history_messages
        max_tool_iterations_getter = getattr(self._llm_client, "max_tool_iterations", None)
        max_tool_iterations = 8
        if callable(max_tool_iterations_getter):
            maybe_value = max_tool_iterations_getter()
            if isinstance(maybe_value, int) and maybe_value > 0:
                max_tool_iterations = maybe_value
        runtime_limits = RuntimeLimits(
            max_steps=max_tool_iterations,
            max_tool_calls=max(12, max_tool_iterations * 2),
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
                )
                answer, should_reply = self._extract_answer(generation.payload)
            else:
                state = self._build_agent_state(
                    history=history,
                    user_text=model_text,
                    user_content=model_user_content,
                )
                generation = await self._runtime.run(
                    state=state,
                    tool_context=tool_context,
                    response_schema=self._response_schema(),
                    prompt_cache_key=prompt_cache_key,
                )
                answer, should_reply = self._extract_answer(generation.payload)
        except Exception as exc:
            self._logger.exception("LLM call failed", exc_info=exc)
            answer = "Sorry, I couldn't answer right now."
            should_reply = True
        await self._memory.append_history(session_id, "assistant", answer)
        await self._enforce_history_limit(session_id)

        chat_id = message.chat_id or message.user_id or 0
        return ChannelResponse(
            channel=message.channel,
            chat_id=chat_id,
            text=answer,
            metadata=self._response_metadata(should_reply),
        )

    def _build_model_user_input(self, message: ChannelMessage) -> tuple[str, str | list[dict[str, Any]] | None]:
        prompt_text = message.text.strip() if message.text else ""
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
    ) -> AgentState:
        system_prompt_getter = getattr(self._llm_client, "system_prompt", None)
        system_prompt = "You are Minibot, a helpful assistant."
        if callable(system_prompt_getter):
            maybe_prompt = system_prompt_getter()
            if isinstance(maybe_prompt, str) and maybe_prompt:
                system_prompt = maybe_prompt
        if any(binding.tool.name == "self_insert_artifact" for binding in self._tools):
            system_prompt = (
                f"{system_prompt}\n"
                "When you need to inspect a local workspace file (image/document), call self_insert_artifact first "
                "to inject it into conversation context before answering file contents."
            )
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
        if not attachment_summary:
            return base_text
        visible_text = base_text or model_text
        if visible_text:
            return f"{visible_text}\nAttachments: {attachment_summary}"
        return f"Attachments: {attachment_summary}"

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

    def _response_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "should_answer_to_user": {"type": "boolean"},
            },
            "required": ["answer", "should_answer_to_user"],
            "additionalProperties": False,
        }

    async def _enforce_history_limit(self, session_id: str) -> None:
        if self._max_history_messages is None:
            return
        await self._memory.trim_history(session_id, self._max_history_messages)

    def _extract_answer(self, payload: Any) -> tuple[str, bool]:
        if isinstance(payload, dict):
            answer = payload.get("answer")
            should = payload.get("should_answer_to_user")
            if isinstance(answer, str) and isinstance(should, bool):
                return answer, should
            result = payload.get("result")
            if isinstance(result, str):
                return result, True
            timestamp = payload.get("timestamp")
            if isinstance(timestamp, str):
                return timestamp, True
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
                if isinstance(answer, str) and isinstance(should, bool):
                    return answer, should
                result = parsed.get("result")
                if isinstance(result, str):
                    return result, True
                timestamp = parsed.get("timestamp")
                if isinstance(timestamp, str):
                    return timestamp, True
            return payload, True
        return str(payload), True


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
