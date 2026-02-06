from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from llm_async.models import Tool

from minibot.core.memory import KeyValueEntry, KeyValueMemory
from minibot.llm.tools.base import ToolBinding, ToolContext


def build_kv_tools(memory: KeyValueMemory) -> list[ToolBinding]:
    return [
        ToolBinding(tool=_save_tool(), handler=lambda payload, ctx: _save_entry(memory, payload, ctx)),
        ToolBinding(tool=_get_tool(), handler=lambda payload, ctx: _get_entry(memory, payload, ctx)),
        ToolBinding(tool=_search_tool(), handler=lambda payload, ctx: _search_entries(memory, payload, ctx)),
    ]


def _save_tool() -> Tool:
    return Tool(
        name="kv_save",
        description="Persist a snippet of text with metadata for later retrieval.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title for the entry", "maxLength": 100},
                "data": {"type": "string", "description": "Full content"},
                "metadata": {
                    "type": ["string", "null"],
                    "description": "Optional JSON metadata",
                },
                "source": {
                    "type": ["string", "null"],
                    "description": "Optional source or URL",
                },
                "expires_at": {
                    "type": ["string", "null"],
                    "description": "ISO datetime when entry expires",
                },
            },
            "required": ["title", "data", "metadata", "source", "expires_at"],
            "additionalProperties": False,
        },
    )


def _get_tool() -> Tool:
    return Tool(
        name="kv_get",
        description="Fetch a single entry by id or title.",
        parameters={
            "type": "object",
            "properties": {
                "entry_id": {"type": ["string", "null"]},
                "title": {"type": ["string", "null"]},
            },
            "required": ["entry_id", "title"],
            "additionalProperties": False,
        },
    )


def _search_tool() -> Tool:
    return Tool(
        name="kv_search",
        description="Search entries by fuzzy text with pagination.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": ["string", "null"],
                    "description": "Text to search in title/data",
                },
                "limit": {"type": ["integer", "null"], "minimum": 1},
                "offset": {"type": ["integer", "null"], "minimum": 0},
            },
            "required": ["query", "limit", "offset"],
            "additionalProperties": False,
        },
    )


async def _save_entry(
    memory: KeyValueMemory,
    payload: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    owner_id = _owner_from_context(context)
    title = _require_str(payload, "title")
    data = _require_str(payload, "data")
    metadata = _coerce_metadata(payload.get("metadata"))
    source = _optional_str(payload.get("source"))
    expires_at = _parse_datetime(payload.get("expires_at"))
    entry = await memory.save_entry(
        owner_id=owner_id,
        title=title,
        data=data,
        metadata=metadata,
        source=source,
        expires_at=expires_at,
    )
    return _entry_payload(entry)


async def _get_entry(
    memory: KeyValueMemory,
    payload: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    owner_id = _owner_from_context(context)
    entry_id = _optional_str(payload.get("entry_id"))
    title = _optional_str(payload.get("title"))
    if not entry_id and not title:
        raise ValueError("entry_id or title is required")
    entry = await memory.get_entry(owner_id=owner_id, entry_id=entry_id, title=title)
    if not entry:
        return {"message": "Entry not found", "owner_id": owner_id}
    return _entry_payload(entry)


async def _search_entries(
    memory: KeyValueMemory,
    payload: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    owner_id = _owner_from_context(context)
    query = _optional_str(payload.get("query"))
    limit = _optional_int(payload.get("limit"))
    offset = _optional_int(payload.get("offset"))
    result = await memory.search_entries(owner_id=owner_id, query=query, limit=limit, offset=offset)
    return {
        "owner_id": owner_id,
        "total": result.total,
        "limit": result.limit,
        "offset": result.offset,
        "entries": [_entry_payload(entry) for entry in result.entries],
    }


def _entry_payload(entry: KeyValueEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "owner_id": entry.owner_id,
        "title": entry.title,
        "data": entry.data,
        "metadata": dict(entry.metadata),
        "source": entry.source,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
        "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
    }


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _owner_from_context(context: ToolContext) -> str:
    if not context.owner_id:
        raise ValueError("owner context is required")
    return context.owner_id


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Expected string value")
    stripped = value.strip()
    return stripped or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return int(stripped)
    raise ValueError("Expected integer value")


def _coerce_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("metadata must deserialize to an object")
        return parsed
    raise ValueError("metadata must be an object or JSON string")


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expires_at must be an ISO datetime string")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
