from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any, Literal, cast

from llm_async.models import Tool

from minibot.core.agent_runtime import AgentMessage, AppendMessageDirective, MessagePart, MessageRole, ToolResult
from minibot.core.channels import ChannelFileResponse
from minibot.core.events import EventPublisher, OutboundFileEvent
from minibot.core.files import FileStorage
from minibot.llm.tools.arg_utils import optional_int, optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_boolean, nullable_integer, nullable_string, strict_object
from minibot.shared.path_utils import to_posix_relative


class FileStorageTool:
    """Managed file operations scoped to a configured root directory.

    Enabled by ``[tools.file_storage]`` in ``config.toml``.

    Exposes nine LLM tools:

    - ``list_files`` — list files and folders under a folder path.
    - ``glob_files`` — list files matching a glob pattern.
    - ``file_info`` — metadata for one path.
    - ``write_file`` — create or overwrite a text file.
    - ``read_file`` — read a full text file.
    - ``move_file`` — move or rename a file.
    - ``delete_file`` — delete a file or folder.
    - ``send_file`` — deliver a file to the active conversation channel.
    - ``self_insert_artifact`` — inject a managed file or image into the active
      conversation context.

    All paths are relative to ``root_dir``. ``allow_outside_root = false``
    prevents path traversal. Incoming uploads are saved to ``uploads_subdir``
    when ``save_incoming_uploads = true``.

    Key config options:

    - ``root_dir`` — managed storage root.
    - ``max_write_bytes`` — per-write size limit.
    - ``allow_outside_root`` — disable path-escape guard (not recommended).
    """

    _IMAGE_MIME_PREFIX = "image/"

    def __init__(
        self,
        storage: FileStorage,
        event_bus: EventPublisher | None = None,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._logger = logging.getLogger("minibot.tools.file_storage")

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._list_files_schema(), handler=self._list_files),
            ToolBinding(tool=self._glob_files_schema(), handler=self._glob_files),
            ToolBinding(tool=self._file_info_schema(), handler=self._file_info),
            ToolBinding(tool=self._write_file_schema(), handler=self._create_file),
            ToolBinding(tool=self._read_file_schema(), handler=self._read_file),
            ToolBinding(tool=self._move_file_schema(), handler=self._move_file),
            ToolBinding(tool=self._delete_file_schema(), handler=self._delete_file),
            ToolBinding(tool=self._send_file_schema(), handler=self._send_file),
            ToolBinding(tool=self._self_insert_artifact_schema(), handler=self._self_insert_artifact),
        ]

    def _list_files_schema(self) -> Tool:
        return Tool(
            name="list_files",
            description=load_tool_description("list_files"),
            parameters=strict_object(
                properties={
                    "folder": nullable_string("Optional folder relative to the managed root. Defaults to root."),
                },
                required=["folder"],
            ),
        )

    def _file_info_schema(self) -> Tool:
        return Tool(
            name="file_info",
            description=load_tool_description("file_info"),
            parameters=strict_object(
                properties={
                    "path": {"type": "string", "description": "Relative file path under the managed root."},
                },
                required=["path"],
            ),
        )

    def _write_file_schema(self) -> Tool:
        return Tool(
            name="write_file",
            description=load_tool_description("write_file"),
            parameters=strict_object(
                properties={
                    "path": {"type": "string", "description": "Relative file path, for example notes/today.md."},
                    "content": {"type": "string", "description": "Full text content to write."},
                    "overwrite": nullable_boolean("Set true to replace an existing file. Defaults to false."),
                },
                required=["path", "content", "overwrite"],
            ),
        )

    def _move_file_schema(self) -> Tool:
        return Tool(
            name="move_file",
            description=load_tool_description("move_file"),
            parameters=strict_object(
                properties={
                    "source_path": {"type": "string", "description": "Relative path of the file to move."},
                    "destination_path": {"type": "string", "description": "Relative destination path."},
                    "overwrite": nullable_boolean("Set true to replace an existing destination file."),
                },
                required=["source_path", "destination_path", "overwrite"],
            ),
        )

    def _delete_file_schema(self) -> Tool:
        return Tool(
            name="delete_file",
            description=load_tool_description("delete_file"),
            parameters=strict_object(
                properties={
                    "path": {"type": "string", "description": "Relative path of the file or folder to delete."},
                    "target": {
                        **nullable_string("Restrict what may be deleted."),
                        "enum": ["any", "file", "folder", None],
                    },
                    "recursive": nullable_boolean("Set true to delete a non-empty folder."),
                },
                required=["path", "target", "recursive"],
            ),
        )

    def _send_file_schema(self) -> Tool:
        return Tool(
            name="send_file",
            description=load_tool_description("send_file"),
            parameters=strict_object(
                properties={
                    "path": {"type": "string", "description": "Relative path of the file to deliver."},
                    "caption": nullable_string("Optional caption sent with the file."),
                },
                required=["path", "caption"],
            ),
        )

    def _glob_files_schema(self) -> Tool:
        return Tool(
            name="glob_files",
            description=load_tool_description("glob_files"),
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

    def _read_file_schema(self) -> Tool:
        return Tool(
            name="read_file",
            description=load_tool_description("read_file"),
            parameters=strict_object(
                properties={"path": {"type": "string", "description": "Relative file path under managed root."}},
                required=["path"],
            ),
        )

    def _self_insert_artifact_schema(self) -> Tool:
        return Tool(
            name="self_insert_artifact",
            description=load_tool_description("self_insert_artifact"),
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
        raw_entries = self._storage.list_files(folder)
        entries = [self._entry_with_canonical_paths(entry) for entry in raw_entries]
        return {
            "action": "list",
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
        raw_entries = self._storage.glob_files(pattern=pattern, folder=folder, limit=limit)
        entries = [self._entry_with_canonical_paths(entry) for entry in raw_entries]
        return {
            "action": "glob",
            "root_dir": str(self._storage.root_dir),
            "folder": folder or ".",
            "pattern": pattern,
            "limit": limit,
            "entries": entries,
            "count": len(entries),
        }

    async def _read_file(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        path = require_non_empty_str(payload, "path")
        result = self._storage.read_text_file(path)
        return {
            "action": "read",
            **result,
            **self._canonical_path_payload(str(result["path"])),
        }

    async def _create_file(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        path = require_non_empty_str(payload, "path")
        content = require_non_empty_str(payload, "content")
        overwrite = bool(payload.get("overwrite") or False)
        result = self._storage.create_text_file(path=path, content=content, overwrite=overwrite)
        return {
            "action": "write",
            "ok": True,
            "path": result["path"],
            "bytes_written": result["bytes_written"],
            "overwrite": overwrite,
            **self._canonical_path_payload(str(result["path"])),
        }

    async def _file_info(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        path = require_non_empty_str(payload, "path")
        info = self._storage.file_info(path)
        return {
            "action": "info",
            "ok": True,
            **info,
            **self._canonical_path_payload(str(info["path"])),
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
            "action": "send",
            "ok": True,
            "path": str(path),
            "chat_id": context.chat_id,
            "channel": context.channel,
            "sent": True,
            **self._canonical_path_payload(str(path)),
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
            "action": "move",
            "ok": True,
            "source_path": result["source_path"],
            "destination_path": result["destination_path"],
            "overwrite": overwrite,
            **self._canonical_path_payload(str(result["source_path"]), prefix="source_"),
            **self._canonical_path_payload(str(result["destination_path"]), prefix="destination_"),
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
            "action": "delete",
            "ok": True,
            "path": result["path"],
            "deleted": bool(result.get("deleted", False)),
            "deleted_count": deleted_count,
            "target": target,
            "recursive": recursive,
            "target_type": target_type,
            "message": message,
            **self._canonical_path_payload(resolved_path),
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
            code = (
                "file_not_found"
                if getattr(exc, "error_code", None) == "file_not_found" or reason == "file does not exist"
                else "invalid_path"
            )
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

    def _entry_with_canonical_paths(self, entry: dict[str, Any]) -> dict[str, Any]:
        path_value = entry.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            return dict(entry)
        return {
            **entry,
            **self._canonical_path_payload(path_value),
        }

    def _canonical_path_payload(self, path_value: str, *, prefix: str = "") -> dict[str, Any]:
        resolved = self._storage.resolve_file(path_value)
        absolute = resolved.resolve().as_posix()
        if resolved.is_relative_to(self._storage.root_dir):
            relative = to_posix_relative(resolved, self._storage.root_dir)
            scope = "inside_root"
        else:
            relative = None
            scope = "outside_root"
        return {
            f"{prefix}path_relative": relative,
            f"{prefix}path_absolute": absolute,
            f"{prefix}path_scope": scope,
        }
