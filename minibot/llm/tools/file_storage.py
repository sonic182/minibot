from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any
from typing import Literal, cast

from llm_async.models import Tool

from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.app.event_bus import EventBus
from minibot.core.agent_runtime import AgentMessage, AppendMessageDirective, MessagePart, MessageRole, ToolResult
from minibot.core.channels import ChannelFileResponse
from minibot.core.events import OutboundFileEvent
from minibot.llm.tools.arg_utils import optional_int, optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.schema_utils import nullable_boolean, nullable_integer, nullable_string, strict_object
from minibot.shared.path_utils import to_posix_relative


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
            parameters=strict_object(
                properties={
                    "folder": nullable_string("Optional folder relative to the managed root. Defaults to root.")
                },
                required=["folder"],
            ),
        )

    def _create_file_schema(self) -> Tool:
        return Tool(
            name="create_file",
            description="Create a text or markdown file under the managed file root.",
            parameters=strict_object(
                properties={
                    "path": {
                        "type": "string",
                        "description": "Relative file path (for example notes/today.md).",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write into the file.",
                    },
                    "overwrite": nullable_boolean("Set true to replace existing files."),
                },
                required=["path", "content", "overwrite"],
            ),
        )

    def _glob_files_schema(self) -> Tool:
        return Tool(
            name="glob_files",
            description="List files matching a glob pattern under the managed file root.",
            parameters=strict_object(
                properties={
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (for example **/*.md or uploads/**/*.png).",
                    },
                    "folder": nullable_string("Optional folder relative to managed root to scope search."),
                    "limit": nullable_integer(
                        minimum=1,
                        description="Maximum number of matches to return. Defaults to all matches.",
                    ),
                },
                required=["pattern", "folder", "limit"],
            ),
        )

    def _file_info_schema(self) -> Tool:
        return Tool(
            name="file_info",
            description="Get basic metadata for a managed file.",
            parameters=strict_object(
                properties={"path": {"type": "string", "description": "Relative file path under managed root."}},
                required=["path"],
            ),
        )

    def _send_file_schema(self) -> Tool:
        return Tool(
            name="send_file",
            description="Send a managed file to the current channel chat.",
            parameters=strict_object(
                properties={
                    "path": {
                        "type": "string",
                        "description": "Relative file path under the managed root.",
                    },
                    "caption": nullable_string("Optional caption sent with the file."),
                },
                required=["path", "caption"],
            ),
        )

    def _move_file_schema(self) -> Tool:
        return Tool(
            name="move_file",
            description="Move or rename a managed file to another managed path.",
            parameters=strict_object(
                properties={
                    "source_path": {
                        "type": "string",
                        "description": "Existing relative file path under managed root.",
                    },
                    "destination_path": {
                        "type": "string",
                        "description": "Target relative file path under managed root.",
                    },
                    "overwrite": nullable_boolean("Set true to replace an existing destination file."),
                },
                required=["source_path", "destination_path", "overwrite"],
            ),
        )

    def _delete_file_schema(self) -> Tool:
        return Tool(
            name="delete_file",
            description="Delete a managed file or folder from disk.",
            parameters=strict_object(
                properties={
                    "path": {
                        "type": "string",
                        "description": "Relative path under managed root.",
                    },
                    "target": {
                        **nullable_string("Target kind filter. Use folder to only delete folders."),
                        "enum": ["any", "file", "folder", None],
                    },
                    "recursive": nullable_boolean("Set true to delete non-empty folders recursively."),
                },
                required=["path", "target", "recursive"],
            ),
        )

    def _self_insert_artifact_schema(self) -> Tool:
        return Tool(
            name="self_insert_artifact",
            description=(
                "Inject a managed file into conversation context for multimodal analysis. "
                "Path must be relative to managed root."
            ),
            parameters=strict_object(
                properties={
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
                        **nullable_string("Target role for injected message. Defaults to user."),
                        "enum": ["user", "system", None],
                    },
                    "text": nullable_string("Optional text prepended before injected file/image part."),
                    "mime": nullable_string("Optional MIME hint."),
                    "filename": nullable_string("Optional display filename for file mode."),
                },
                required=["path", "as", "role", "text", "mime", "filename"],
            ),
        )

    async def _list_files(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        folder = optional_str(payload.get("folder"))
        entries = self._storage.list_files(folder)
        return {
            "root_dir": str(self._storage.root_dir),
            "folder": folder or ".",
            "entries": entries,
            "count": len(entries),
        }

    async def _glob_files(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        pattern = require_non_empty_str(payload, "pattern")
        folder = optional_str(payload.get("folder"))
        limit = optional_int(
            payload.get("limit"),
            field="limit",
            min_value=1,
            allow_float=False,
            allow_string=False,
            type_error="Expected integer value",
            min_error="Expected integer value >= 1",
        )
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
        path = require_non_empty_str(payload, "path")
        content = require_non_empty_str(payload, "content")
        overwrite = bool(payload.get("overwrite") or False)
        result = self._storage.create_text_file(path=path, content=content, overwrite=overwrite)
        return {
            "ok": True,
            "path": result["path"],
            "bytes_written": result["bytes_written"],
            "overwrite": overwrite,
        }

    async def _file_info(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        path = require_non_empty_str(payload, "path")
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

        path = require_non_empty_str(payload, "path")
        caption = optional_str(payload.get("caption"))
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
        source_path = require_non_empty_str(payload, "source_path")
        destination_path = require_non_empty_str(payload, "destination_path")
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
        path = require_non_empty_str(payload, "path")
        target = (optional_str(payload.get("target")) or "any").lower()
        if target not in {"any", "file", "folder"}:
            raise ValueError("target must be one of: any, file, folder")
        recursive = bool(payload.get("recursive") or False)
        resolved_target = cast(Literal["any", "file", "folder"], target)
        result = self._storage.delete_file(path, recursive=recursive, target=resolved_target)
        deleted_count = int(result.get("deleted_count", 0))
        resolved_path = str(result["path"])
        target_type = str(result.get("target_type") or "path")
        message = (
            f"Deleted {target_type} successfully: {resolved_path}"
            if deleted_count > 0
            else f"No file or folder found to delete: {resolved_path}"
        )
        return {
            "ok": True,
            "path": result["path"],
            "deleted": bool(result.get("deleted", False)),
            "deleted_count": deleted_count,
            "target": target,
            "recursive": recursive,
            "target_type": target_type,
            "message": message,
        }

    async def _self_insert_artifact(self, payload: dict[str, Any], _: ToolContext) -> ToolResult:
        path = require_non_empty_str(payload, "path")
        insert_as = require_non_empty_str(payload, "as").lower()
        if insert_as not in {"image", "file"}:
            return ToolResult(
                content={
                    "status": "error",
                    "code": "invalid_as",
                    "message": "as must be either 'image' or 'file'",
                }
            )
        role = optional_str(payload.get("role")) or "user"
        if role not in {"user", "system"}:
            return ToolResult(
                content={
                    "status": "error",
                    "code": "invalid_role",
                    "message": "role must be user or system",
                }
            )

        text = optional_str(payload.get("text"))
        mime_hint = optional_str(payload.get("mime"))
        filename_hint = optional_str(payload.get("filename"))
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

        relative_path = to_posix_relative(absolute_path, self._storage.root_dir)
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
