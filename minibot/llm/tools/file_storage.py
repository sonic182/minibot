from __future__ import annotations

from typing import Any

from llm_async.models import Tool

from minibot.core.files import FileStorage
from minibot.llm.tools.base import ToolBinding, ToolContext


class FileStorageTool:
    def __init__(self, storage: FileStorage) -> None:
        self._storage = storage

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._write_schema(), handler=self._handle_write),
            ToolBinding(tool=self._list_schema(), handler=self._handle_list),
            ToolBinding(tool=self._read_schema(), handler=self._handle_read),
        ]

    @staticmethod
    def _write_schema() -> Tool:
        return Tool(
            name="file_write",
            description="Write UTF-8 text content to a file in managed workspace (overwrite mode).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path inside managed workspace."},
                    "content": {"type": "string", "description": "Text content to write as UTF-8."},
                    "source": {"type": ["string", "null"], "description": "Optional provenance tag."},
                },
                "required": ["path", "content", "source"],
                "additionalProperties": False,
            },
        )

    @staticmethod
    def _list_schema() -> Tool:
        return Tool(
            name="file_list",
            description="List files in managed workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "prefix": {"type": ["string", "null"], "description": "Optional relative directory prefix."},
                    "limit": {"type": ["integer", "null"], "minimum": 1},
                    "offset": {"type": ["integer", "null"], "minimum": 0},
                },
                "required": ["prefix", "limit", "offset"],
                "additionalProperties": False,
            },
        )

    @staticmethod
    def _read_schema() -> Tool:
        return Tool(
            name="file_read",
            description="Read a managed file by line range or byte range.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path inside managed workspace."},
                    "mode": {
                        "type": ["string", "null"],
                        "enum": ["lines", "bytes", None],
                        "description": "Read mode. Defaults to lines.",
                    },
                    "offset": {"type": ["integer", "null"], "minimum": 0},
                    "limit": {"type": ["integer", "null"], "minimum": 1},
                },
                "required": ["path", "mode", "offset", "limit"],
                "additionalProperties": False,
            },
        )

    async def _handle_write(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = _require_string(payload.get("path"), "path")
        content = payload.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        source = payload.get("source")
        source_tag = "manual" if source is None else _require_string(source, "source")
        record = await self._storage.write_text(
            path=path,
            content=content,
            owner_id=context.owner_id,
            channel=context.channel,
            chat_id=context.chat_id,
            user_id=context.user_id,
            source=source_tag,
        )
        return {"ok": True, "file": record.model_dump(mode="json")}

    async def _handle_list(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        del context
        prefix = payload.get("prefix")
        if prefix is not None and not isinstance(prefix, str):
            raise ValueError("prefix must be a string")
        limit = _optional_int(payload.get("limit"), default=20, field="limit")
        offset = _optional_int(payload.get("offset"), default=0, field="offset")
        files = await self._storage.list_files(prefix=prefix, limit=limit, offset=offset)
        return {
            "ok": True,
            "count": len(files),
            "files": [record.model_dump(mode="json") for record in files],
        }

    async def _handle_read(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        del context
        path = _require_string(payload.get("path"), "path")
        mode = payload.get("mode")
        if mode is None:
            resolved_mode = "lines"
        elif isinstance(mode, str):
            resolved_mode = mode.strip().lower()
        else:
            raise ValueError("mode must be a string")
        if resolved_mode not in {"lines", "bytes"}:
            raise ValueError("mode must be 'lines' or 'bytes'")
        offset = _optional_int(payload.get("offset"), default=0, field="offset")
        limit = _optional_int(payload.get("limit"), default=200, field="limit")
        response = await self._storage.read_file(path=path, mode=resolved_mode, offset=offset, limit=limit)
        return {"ok": True, **response.model_dump(mode="json")}


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _optional_int(value: Any, *, default: int, field: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return default
        return int(normalized)
    raise ValueError(f"{field} must be numeric")
