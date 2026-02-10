from __future__ import annotations

from typing import Any

from llm_async.models import Tool

from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelFileResponse
from minibot.core.events import OutboundFileEvent
from minibot.llm.tools.base import ToolBinding, ToolContext


class FileStorageTool:
    def __init__(self, storage: LocalFileStorage, event_bus: EventBus | None = None) -> None:
        self._storage = storage
        self._event_bus = event_bus

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._list_files_schema(), handler=self._list_files),
            ToolBinding(tool=self._create_file_schema(), handler=self._create_file),
            ToolBinding(tool=self._send_file_schema(), handler=self._send_file),
        ]

    def _list_files_schema(self) -> Tool:
        return Tool(
            name="list_files",
            description="List files and folders under the managed file root.",
            parameters={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": ["string", "null"],
                        "description": "Optional folder relative to the managed root. Defaults to root.",
                    }
                },
                "required": ["folder"],
                "additionalProperties": False,
            },
        )

    def _create_file_schema(self) -> Tool:
        return Tool(
            name="create_file",
            description="Create a text or markdown file under the managed file root.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path (for example notes/today.md).",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write into the file.",
                    },
                    "overwrite": {
                        "type": ["boolean", "null"],
                        "description": "Set true to replace existing files.",
                    },
                },
                "required": ["path", "content", "overwrite"],
                "additionalProperties": False,
            },
        )

    def _send_file_schema(self) -> Tool:
        return Tool(
            name="send_file",
            description="Send a managed file to the current channel chat.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path under the managed root.",
                    },
                    "caption": {
                        "type": ["string", "null"],
                        "description": "Optional caption sent with the file.",
                    },
                },
                "required": ["path", "caption"],
                "additionalProperties": False,
            },
        )

    async def _list_files(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        folder = self._optional_str(payload.get("folder"))
        entries = self._storage.list_files(folder)
        return {
            "root_dir": str(self._storage.root_dir),
            "folder": folder or ".",
            "entries": entries,
            "count": len(entries),
        }

    async def _create_file(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        path = self._require_str(payload, "path")
        content = payload.get("content")
        overwrite = bool(payload.get("overwrite") or False)
        result = self._storage.create_text_file(path=path, content=content, overwrite=overwrite)
        return {
            "ok": True,
            "path": result["path"],
            "bytes_written": result["bytes_written"],
            "overwrite": overwrite,
        }

    async def _send_file(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        if self._event_bus is None:
            raise ValueError("send_file is unavailable because no event bus is configured")
        if not context.channel:
            raise ValueError("channel context is required")
        if context.chat_id is None:
            raise ValueError("chat context is required")

        path = self._require_str(payload, "path")
        caption = self._optional_str(payload.get("caption"))
        absolute_path = self._storage.resolve_existing_file(path)

        await self._event_bus.publish(
            OutboundFileEvent(
                response=ChannelFileResponse(
                    channel=context.channel,
                    chat_id=context.chat_id,
                    file_path=str(absolute_path),
                    caption=caption,
                )
            )
        )
        return {
            "ok": True,
            "path": str(path),
            "chat_id": context.chat_id,
            "channel": context.channel,
            "sent": True,
        }

    @staticmethod
    def _require_str(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("Expected string value")
        stripped = value.strip()
        return stripped or None
