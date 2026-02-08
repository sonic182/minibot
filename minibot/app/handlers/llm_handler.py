from __future__ import annotations

import logging
from typing import Any, Sequence

import json

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
        self._logger = logging.getLogger("minibot.handler")

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
                    "attachment_types": [
                        str(attachment.get("type", "unknown")) for attachment in message.attachments
                    ],
                    "responses_provider": self._llm_client.is_responses_provider(),
                },
            )
        await self._memory.append_history(session_id, "user", self._build_history_user_entry(message, model_text))
        await self._enforce_history_limit(session_id)

        if message.attachments and not self._llm_client.is_responses_provider():
            answer = "Media inputs are only supported when `llm.provider` is `openai_responses`."
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
        parts: list[dict[str, Any]] = [{"type": "input_text", "text": resolved_prompt}]
        parts.extend(message.attachments)
        return resolved_prompt, parts

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
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    answer = parsed.get("answer")
                    should = parsed.get("should_answer_to_user")
                    if isinstance(answer, str) and isinstance(should, bool):
                        return answer, should
            except Exception:
                pass
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
