from __future__ import annotations

import json
from typing import Any

from llm_async.models import Tool

from minibot.core.memory import KeyValueEntry, KeyValueMemory
from minibot.llm.tools.arg_utils import optional_int, optional_str, require_non_empty_str, require_owner
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import (
    nullable_string,
    pagination_properties,
    strict_object,
)
from minibot.shared.datetime_utils import parse_optional_iso_datetime_utc


def build_kv_tools(memory: KeyValueMemory) -> list[ToolBinding]:
    return [
        ToolBinding(tool=_memory_tool(), handler=lambda payload, ctx: _memory_action(memory, payload, ctx)),
    ]


def _memory_tool() -> Tool:
    return Tool(
        name="memory",
        description=load_tool_description("memory"),
        parameters=strict_object(
            properties={
                "action": {
                    "type": "string",
                    "enum": ["save", "get", "search", "delete"],
                    "description": "Memory operation to perform.",
                },
                "entry_id": nullable_string("Entry id for get/delete."),
                "title": nullable_string("Entry title for save/get/delete."),
                "data": nullable_string("Entry content for save."),
                "query": nullable_string("Search query for search."),
                "metadata": nullable_string("Optional JSON metadata for save."),
                "source": nullable_string("Optional source for save."),
                "expires_at": nullable_string("ISO datetime when entry expires for save."),
                **pagination_properties(),
            },
            required=[
                "action",
                "entry_id",
                "title",
                "data",
                "query",
                "metadata",
                "source",
                "expires_at",
                "limit",
                "offset",
            ],
        ),
    )


async def _memory_action(memory: KeyValueMemory, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    action = optional_str(payload.get("action"))
    if action == "save":
        return await _save_entry(memory, payload, context)
    if action == "get":
        return await _get_entry(memory, payload, context)
    if action == "search":
        return await _search_entries(memory, payload, context)
    if action == "delete":
        return await _delete_entry(memory, payload, context)
    raise ValueError("action must be one of: save, get, search, delete")


async def _save_entry(
    memory: KeyValueMemory,
    payload: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    owner_id = require_owner(context)
    title = require_non_empty_str(payload, "title")
    data = require_non_empty_str(payload, "data")
    metadata = _coerce_metadata(payload.get("metadata"))
    source = optional_str(payload.get("source"))
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
    owner_id = require_owner(context)
    entry_id = optional_str(payload.get("entry_id"))
    title = optional_str(payload.get("title"))
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
    owner_id = require_owner(context)
    query = optional_str(payload.get("query"))
    limit = optional_int(
        payload.get("limit"),
        field="limit",
        allow_float=True,
        allow_string=True,
        reject_bool=False,
        type_error="Expected integer value",
    )
    offset = optional_int(
        payload.get("offset"),
        field="offset",
        allow_float=True,
        allow_string=True,
        reject_bool=False,
        type_error="Expected integer value",
    )
    result = await memory.search_entries(owner_id=owner_id, query=query, limit=limit, offset=offset)
    return {
        "owner_id": owner_id,
        "total": result.total,
        "limit": result.limit,
        "offset": result.offset,
        "entries": [_entry_payload(entry) for entry in result.entries],
    }


async def _delete_entry(
    memory: KeyValueMemory,
    payload: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    owner_id = require_owner(context)
    entry_id = optional_str(payload.get("entry_id"))
    title = optional_str(payload.get("title"))
    if not entry_id and not title:
        raise ValueError("entry_id or title is required")
    deleted = await memory.delete_entry(owner_id=owner_id, entry_id=entry_id, title=title)
    return {
        "owner_id": owner_id,
        "deleted": deleted,
        "entry_id": entry_id,
        "title": title,
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


def _parse_datetime(value: Any):
    return parse_optional_iso_datetime_utc(value, field="expires_at")
