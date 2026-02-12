from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any
from typing import cast

from llm_async.models import Tool

from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.app.event_bus import EventBus
from minibot.core.agent_runtime import AgentMessage, AppendMessageDirective, MessagePart, MessageRole, ToolResult
from minibot.core.channels import ChannelFileResponse
from minibot.core.events import OutboundFileEvent
from minibot.llm.tools.base import ToolBinding, ToolContext


class FileStorageTool:
    _IMAGE_MIME_PREFIX = "image/"

    def __init__(
        self,
        storage: LocalFileStorage,
        event_bus: EventBus | None = None,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._logger = logging.getLogger("minibot.tools.file_storage")

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._list_files_schema(), handler=self._list_files),
            ToolBinding(tool=self._glob_files_schema(), handler=self._glob_files),
            ToolBinding(tool=self._file_info_schema(), handler=self._file_info),
            ToolBinding(tool=self._create_file_schema(), handler=self._create_file),
            ToolBinding(tool=self._move_file_schema(), handler=self._move_file),
            ToolBinding(tool=self._delete_file_schema(), handler=self._delete_file),
            ToolBinding(tool=self._send_file_schema(), handler=self._send_file),
            ToolBinding(tool=self._self_insert_artifact_schema(), handler=self._self_insert_artifact),
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

    def _glob_files_schema(self) -> Tool:
        return Tool(
            name="glob_files",
            description="List files matching a glob pattern under the managed file root.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (for example **/*.md or uploads/**/*.png).",
                    },
                    "folder": {
                        "type": ["string", "null"],
                        "description": "Optional folder relative to managed root to scope search.",
                    },
                    "limit": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Maximum number of matches to return. Defaults to all matches.",
                    },
                },
                "required": ["pattern", "folder", "limit"],
                "additionalProperties": False,
            },
        )

    def _file_info_schema(self) -> Tool:
        return Tool(
            name="file_info",
            description="Get basic metadata for a managed file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path under managed root.",
                    }
                },
                "required": ["path"],
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

    def _move_file_schema(self) -> Tool:
        return Tool(
            name="move_file",
            description="Move or rename a managed file to another managed path.",
            parameters={
                "type": "object",
                "properties": {
                    "source_path": {
                        "type": "string",
                        "description": "Existing relative file path under managed root.",
                    },
                    "destination_path": {
                        "type": "string",
                        "description": "Target relative file path under managed root.",
                    },
                    "overwrite": {
                        "type": ["boolean", "null"],
                        "description": "Set true to replace an existing destination file.",
                    },
                },
                "required": ["source_path", "destination_path", "overwrite"],
                "additionalProperties": False,
            },
        )

    def _delete_file_schema(self) -> Tool:
        return Tool(
            name="delete_file",
            description="Delete a managed file from disk.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Existing relative file path under managed root.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        )

    def _self_insert_artifact_schema(self) -> Tool:
        return Tool(
            name="self_insert_artifact",
            description=(
                "Inject a managed file into conversation context for multimodal analysis. "
                "Path must be relative to managed root."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path under managed root (for example uploads/friends.jpg).",
                    },
                    "as": {
                        "type": "string",
                        "enum": ["image", "file"],
                        "description": "How to represent this file in injected message content.",
                    },
                    "role": {
                        "type": ["string", "null"],
                        "enum": ["user", "system", None],
                        "description": "Target role for injected message. Defaults to user.",
                    },
                    "text": {
                        "type": ["string", "null"],
                        "description": "Optional text prepended before injected file/image part.",
                    },
                    "mime": {
                        "type": ["string", "null"],
                        "description": "Optional MIME hint.",
                    },
                    "filename": {
                        "type": ["string", "null"],
                        "description": "Optional display filename for file mode.",
                    },
                },
                "required": ["path", "as", "role", "text", "mime", "filename"],
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

    async def _glob_files(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        pattern = self._require_str(payload, "pattern")
        folder = self._optional_str(payload.get("folder"))
        limit = self._optional_int(payload.get("limit"))
        entries = self._storage.glob_files(pattern=pattern, folder=folder, limit=limit)
        return {
            "root_dir": str(self._storage.root_dir),
            "folder": folder or ".",
            "pattern": pattern,
            "limit": limit,
            "entries": entries,
            "count": len(entries),
        }

    async def _create_file(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        path = self._require_str(payload, "path")
        content = self._require_str(payload, "content")
        overwrite = bool(payload.get("overwrite") or False)
        result = self._storage.create_text_file(path=path, content=content, overwrite=overwrite)
        return {
            "ok": True,
            "path": result["path"],
            "bytes_written": result["bytes_written"],
            "overwrite": overwrite,
        }

    async def _file_info(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        path = self._require_str(payload, "path")
        info = self._storage.file_info(path)
        return {
            "ok": True,
            **info,
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

    async def _move_file(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        source_path = self._require_str(payload, "source_path")
        destination_path = self._require_str(payload, "destination_path")
        overwrite = bool(payload.get("overwrite") or False)
        result = self._storage.move_file(
            source_path=source_path,
            destination_path=destination_path,
            overwrite=overwrite,
        )
        return {
            "ok": True,
            "source_path": result["source_path"],
            "destination_path": result["destination_path"],
            "overwrite": overwrite,
        }

    async def _delete_file(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        path = self._require_str(payload, "path")
        result = self._storage.delete_file(path)
        return {
            "ok": True,
            "path": result["path"],
            "deleted": True,
        }

    async def _self_insert_artifact(self, payload: dict[str, Any], _: ToolContext) -> ToolResult:
        path = self._require_str(payload, "path")
        insert_as = self._require_str(payload, "as").lower()
        if insert_as not in {"image", "file"}:
            return ToolResult(
                content={
                    "status": "error",
                    "code": "invalid_as",
                    "message": "as must be either 'image' or 'file'",
                }
            )
        role = self._optional_str(payload.get("role")) or "user"
        if role not in {"user", "system"}:
            return ToolResult(
                content={
                    "status": "error",
                    "code": "invalid_role",
                    "message": "role must be user or system",
                }
            )

        text = self._optional_str(payload.get("text"))
        mime_hint = self._optional_str(payload.get("mime"))
        filename_hint = self._optional_str(payload.get("filename"))
        self._logger.debug(
            "self_insert_artifact resolving managed path",
            extra={"path": path, "managed_root": str(self._storage.root_dir)},
        )

        try:
            absolute_path = self._storage.resolve_existing_file(path)
        except ValueError as exc:
            reason = str(exc)
            self._logger.debug(
                "self_insert_artifact rejected input",
                extra={"path": path, "reason": reason},
            )
            if reason == "file does not exist":
                code = "file_not_found"
            elif reason in {
                "path is not a file",
                "path must be relative to managed root",
                "path escapes managed root",
            }:
                code = "invalid_path"
            else:
                code = "invalid_path"
            return ToolResult(content={"status": "error", "code": code, "message": reason})

        relative_path = str(absolute_path.relative_to(self._storage.root_dir)).replace("\\", "/")
        resolved_mime = self._resolve_mime(absolute_path, mime_hint)
        filename = filename_hint or absolute_path.name
        file_size = absolute_path.stat().st_size

        if insert_as == "image" and not resolved_mime.startswith(self._IMAGE_MIME_PREFIX):
            self._logger.debug(
                "self_insert_artifact rejected non-image mime for image mode",
                extra={"path": relative_path, "mime": resolved_mime},
            )
            return ToolResult(
                content={
                    "status": "error",
                    "code": "unsupported_mime",
                    "message": f"managed file MIME {resolved_mime} is not an image",
                }
            )

        parts: list[MessagePart] = []
        if text is not None:
            parts.append(MessagePart(type="text", text=text))
        source = {"type": "managed_file", "path": relative_path}
        if insert_as == "image":
            parts.append(MessagePart(type="image", source=source, mime=resolved_mime))
        else:
            parts.append(MessagePart(type="file", source=source, mime=resolved_mime, filename=filename))

        directives = [
            AppendMessageDirective(
                type="append_message",
                message=AgentMessage(role=cast(MessageRole, role), content=parts),
            )
        ]
        self._logger.debug(
            "self_insert_artifact created append_message directive",
            extra={
                "path": relative_path,
                "mime": resolved_mime,
                "size": file_size,
                "insert_as": insert_as,
                "role": role,
            },
        )
        return ToolResult(
            content={
                "status": "ok",
                "path": relative_path,
                "mime": resolved_mime,
                "size": file_size,
            },
            directives=directives,
        )

    @staticmethod
    def _resolve_mime(path: Path, mime_hint: str | None) -> str:
        if isinstance(mime_hint, str) and mime_hint.strip():
            return mime_hint.strip().lower()
        guessed, _ = mimetypes.guess_type(str(path), strict=False)
        if isinstance(guessed, str) and guessed:
            return guessed.lower()
        return "application/octet-stream"

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

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Expected integer value")
        if value < 1:
            raise ValueError("Expected integer value >= 1")
        return value
