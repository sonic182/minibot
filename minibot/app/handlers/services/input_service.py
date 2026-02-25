from __future__ import annotations

from typing import Any, Sequence

from minibot.app.incoming_files_context import build_incoming_files_text, incoming_files_from_metadata
from minibot.core.channels import ChannelMessage
from minibot.llm.provider_factory import LLMClient


class UserInputService:
    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def supports_media_inputs(self) -> bool:
        supports_getter = getattr(self._llm_client, "supports_media_inputs", None)
        if callable(supports_getter):
            return bool(supports_getter())
        return self._llm_client.is_responses_provider()

    def media_input_mode(self) -> str:
        mode_getter = getattr(self._llm_client, "media_input_mode", None)
        if callable(mode_getter):
            mode = mode_getter()
            if isinstance(mode, str) and mode:
                return mode
        return "responses" if self._llm_client.is_responses_provider() else "none"

    def build_model_user_input(
        self,
        message: ChannelMessage,
    ) -> tuple[str, str | list[dict[str, Any]] | None]:
        prompt_text = message.text.strip() if message.text else ""
        incoming_files = incoming_files_from_metadata(message.metadata)
        if incoming_files and not message.attachments:
            return build_incoming_files_text(prompt_text, incoming_files), None
        if not message.attachments:
            return prompt_text, None

        resolved_prompt = prompt_text or "Please analyze the attached media and summarize the key information."
        mode = self.media_input_mode()
        parts: list[dict[str, Any]] = []
        if mode == "chat_completions":
            parts.append({"type": "text", "text": resolved_prompt})
        else:
            parts.append({"type": "input_text", "text": resolved_prompt})
        parts.extend(self._transform_attachments_for_mode(message.attachments, mode))
        return resolved_prompt, parts

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
