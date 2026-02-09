from __future__ import annotations

from typing import Any

from llm_async.models import Tool

from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMediaResponse
from minibot.core.events import OutboundMediaEvent
from minibot.core.files import FileStorage
from minibot.llm.tools.base import ToolBinding, ToolContext


class ChannelFileSenderTool:
    def __init__(self, event_bus: EventBus, storage: FileStorage) -> None:
        self._event_bus = event_bus
        self._storage = storage

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._send_schema(), handler=self._handle_send)]

    @staticmethod
    def _send_schema() -> Tool:
        return Tool(
            name="send_file_in_channel",
            description="Send a file from managed workspace through the current channel.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path inside managed workspace."},
                    "media_type": {
                        "type": "string",
                        "enum": ["photo", "document"],
                        "description": "Channel media type.",
                    },
                    "caption": {"type": ["string", "null"], "description": "Optional caption text."},
                },
                "required": ["path", "media_type", "caption"],
                "additionalProperties": False,
            },
        )

    async def _handle_send(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        channel = _require_string(context.channel, "channel context")
        if context.chat_id is None:
            raise ValueError("chat context is required")
        relative_path = _require_string(payload.get("path"), "path")
        media_type = _require_string(payload.get("media_type"), "media_type").lower()
        if media_type not in {"photo", "document"}:
            raise ValueError("media_type must be one of: photo, document")
        caption = payload.get("caption")
        if caption is not None and not isinstance(caption, str):
            raise ValueError("caption must be a string")

        absolute_path = self._storage.resolve_absolute_path(relative_path)
        file_record = self._storage.describe_file(
            path=relative_path,
            owner_id=context.owner_id,
            channel=context.channel,
            chat_id=context.chat_id,
            user_id=context.user_id,
            source="manual",
        )
        response = ChannelMediaResponse(
            channel=channel,
            chat_id=context.chat_id,
            media_type=media_type,
            file_path=str(absolute_path),
            caption=caption,
            filename=absolute_path.name,
            metadata={
                "relative_path": relative_path,
                "file_id": file_record.id,
                "mime_type": file_record.mime_type,
                "size_bytes": file_record.size_bytes,
            },
        )
        await self._event_bus.publish(OutboundMediaEvent(response=response))
        return {
            "ok": True,
            "sent": True,
            "channel": channel,
            "chat_id": context.chat_id,
            "media_type": media_type,
            "file": file_record.model_dump(mode="json"),
        }


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized
