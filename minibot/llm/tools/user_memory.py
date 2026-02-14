from __future__ import annotations

import json
from typing import Any

from llm_async.models import Tool

from minibot.core.memory import KeyValueEntry, KeyValueMemory
from minibot.llm.tools.arg_utils import optional_int, optional_str, require_non_empty_str, require_owner
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.schema_utils import (
    nullable_string,
    pagination_properties,
    selector_entry_id_title_properties,
    strict_object,
)
from minibot.shared.datetime_utils import parse_optional_iso_datetime_utc


def build_kv_tools(memory: KeyValueMemory) -> list[ToolBinding]:
    return [
        ToolBinding(tool=_save_tool(), handler=lambda payload, ctx: _save_entry(memory, payload, ctx)),
        ToolBinding(tool=_get_tool(), handler=lambda payload, ctx: _get_entry(memory, payload, ctx)),
        ToolBinding(tool=_search_tool(), handler=lambda payload, ctx: _search_entries(memory, payload, ctx)),
        ToolBinding(tool=_delete_tool(), handler=lambda payload, ctx: _delete_entry(memory, payload, ctx)),
    ]


def _save_tool() -> Tool:
    return Tool(
        name="user_memory_save",
        description=(
            "Save important information about the current user for long-term memory. "
            "Use this to remember user preferences, personal facts, context, goals, "
            "or any other information that should persist across different conversations. "
            "Examples: job title, interests, important dates, contact preferences, project details. "
            "Do NOT use this for storing conversation messages - use chat_history tools for conversation management."
        ),
        parameters=strict_object(
            properties={
                "title": {"type": "string", "description": "Short title for the entry", "maxLength": 100},
                "data": {"type": "string", "description": "Full content"},
                "metadata": nullable_string("Optional JSON metadata"),
                "source": nullable_string("Optional source or URL"),
                "expires_at": nullable_string("ISO datetime when entry expires"),
            },
            required=["title", "data", "metadata", "source", "expires_at"],
        ),
    )


def _get_tool() -> Tool:
    return Tool(
        name="user_memory_get",
        description=(
            "Retrieve a specific saved user memory entry by its unique ID or title. "
            "Use this when you need to recall particular information you previously saved about the user. "
            "This accesses long-term user memory, not conversation history."
        ),
        parameters=strict_object(properties=selector_entry_id_title_properties(), required=["entry_id", "title"]),
    )


def _delete_tool() -> Tool:
    return Tool(
        name="user_memory_delete",
        description=(
            "Delete a specific saved user memory entry by unique ID or title. "
            "Use this when stored long-term user information is outdated or should be removed."
        ),
        parameters=strict_object(properties=selector_entry_id_title_properties(), required=["entry_id", "title"]),
    )


def _search_tool() -> Tool:
    return Tool(
        name="user_memory_search",
        description=(
            "Search through all saved user memory entries using flexible text matching. "
            "Use this to find relevant information about the user without knowing the exact entry ID or title. "
            "This searches long-term user memory, not conversation history."
        ),
        parameters=strict_object(
            properties={
                "query": nullable_string("Text to search in title/data"),
                **pagination_properties(),
            },
            required=["query", "limit", "offset"],
        ),
    )


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
