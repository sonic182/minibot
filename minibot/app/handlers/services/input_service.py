from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from minibot.app.incoming_files_context import build_incoming_files_text, incoming_files_from_metadata
from minibot.core.channels import ChannelMessage
from minibot.llm.services import LLMExecutionProfile


class UserInputService:
    def __init__(self, llm_client: Any) -> None:
        self._profile = LLMExecutionProfile.from_client(llm_client)

    def supports_media_inputs(self) -> bool:
        return self._profile.supports_media_inputs

    def media_input_mode(self) -> str:
        return self._profile.media_input_mode

    def build_model_user_input(
        self,
        message: ChannelMessage,
    ) -> tuple[str, str | list[dict[str, Any]] | None]:
        message_text = message.text.strip() if message.text else ""
        reply_context = self._reply_context(message)
        prompt_text = self._with_reply_context(message_text, reply_context)
        incoming_files = incoming_files_from_metadata(message.metadata)
        if incoming_files and not message.attachments:
            return build_incoming_files_text(prompt_text, incoming_files), None
        if not message.attachments:
            return prompt_text, None

        resolved_prompt = self._with_reply_context(
            message_text or "Please analyze the attached media and summarize the key information.",
            reply_context,
        )
        mode = self.media_input_mode()
        parts: list[dict[str, Any]] = []
        if mode == "chat_completions":
            parts.append({"type": "text", "text": resolved_prompt})
        else:
            parts.append({"type": "input_text", "text": resolved_prompt})
        parts.extend(self._transform_attachments_for_mode(message.attachments, mode))
        return resolved_prompt, parts

    @staticmethod
    def _reply_context(message: ChannelMessage) -> str:
        reply_to = message.metadata.get("reply_to")
        if not isinstance(reply_to, dict):
            return ""
        lines = ["Telegram reply context:"]
        username = reply_to.get("username")
        if isinstance(username, str) and username:
            lines.append(f"Author: @{username}")
        text = reply_to.get("text")
        if isinstance(text, str) and text:
            lines.append(f"Original message:\n{text}")
        quote = reply_to.get("quote")
        if isinstance(quote, str) and quote:
            lines.append(f"Selected quote:\n{quote}")
        return "\n".join(lines) if len(lines) > 1 else ""

    @staticmethod
    def _with_reply_context(message_text: str, reply_context: str) -> str:
        if not reply_context:
            return message_text
        if not message_text:
            return reply_context
        return f"{reply_context}\n\nCurrent user message:\n{message_text}"

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
