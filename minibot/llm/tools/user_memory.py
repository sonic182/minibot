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
                    "enum": ["save", "get", "search", "delete", "list_titles"],
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
    if action == "list_titles":
        return await _list_titles(memory, payload, context)
    raise ValueError("action must be one of: save, get, search, delete, list_titles")


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
        response: dict[str, Any] = {"message": "Entry not found", "owner_id": owner_id}
        if title:
            suggestions = await _suggest_titles(memory, owner_id, title)
            if suggestions:
                response["suggested_titles"] = suggestions
        return response
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


async def _list_titles(
    memory: KeyValueMemory,
    payload: dict[str, Any],
    context: ToolContext,
) -> dict[str, Any]:
    owner_id = require_owner(context)
    limit_value = optional_int(
        payload.get("limit"),
        field="limit",
        allow_float=True,
        allow_string=True,
        reject_bool=False,
        type_error="Expected integer value",
    )
    offset_value = optional_int(
        payload.get("offset"),
        field="offset",
        allow_float=True,
        allow_string=True,
        reject_bool=False,
        type_error="Expected integer value",
    )
    query = optional_str(payload.get("query"))
    requested_limit = limit_value or 200
    requested_offset = offset_value or 0
    entries, total = await _collect_entries_window(
        memory=memory,
        owner_id=owner_id,
        offset=requested_offset,
        limit=requested_limit,
    )
    if query:
        normalized_query = query.strip().lower()
        entries = [entry for entry in entries if normalized_query in entry.title.lower()]
    titles = [
        {
            "id": entry.id,
            "title": entry.title,
            "updated_at": entry.updated_at.isoformat(),
            "source": entry.source,
        }
        for entry in entries
    ]
    return {
        "owner_id": owner_id,
        "total": len(titles) if query else total,
        "limit": requested_limit,
        "offset": requested_offset,
        "titles": titles,
    }


async def _suggest_titles(memory: KeyValueMemory, owner_id: str, title: str) -> list[str]:
    entries, _ = await _collect_entries_window(memory=memory, owner_id=owner_id, offset=0, limit=200)
    normalized_title = title.strip().lower()
    if not normalized_title:
        return []
    terms = [term for term in normalized_title.split() if term]
    scored: list[tuple[int, str]] = []
    for entry in entries:
        entry_title = entry.title.strip()
        if not entry_title:
            continue
        normalized_entry_title = entry_title.lower()
        score = 0
        if normalized_entry_title == normalized_title:
            score += 100
        if normalized_title in normalized_entry_title:
            score += 20
        if normalized_entry_title.startswith(normalized_title):
            score += 10
        score += sum(1 for term in terms if term and term in normalized_entry_title)
        if score > 0:
            scored.append((score, entry_title))
    scored.sort(key=lambda item: (-item[0], item[1]))
    seen: set[str] = set()
    suggestions: list[str] = []
    for _, candidate in scored:
        if candidate in seen:
            continue
        seen.add(candidate)
        suggestions.append(candidate)
        if len(suggestions) >= 5:
            break
    return suggestions


async def _collect_entries_window(
    memory: KeyValueMemory,
    owner_id: str,
    offset: int,
    limit: int,
) -> tuple[list[KeyValueEntry], int]:
    entries: list[KeyValueEntry] = []
    total = 0
    next_offset = max(offset, 0)
    while len(entries) < limit:
        page = await memory.list_entries(owner_id=owner_id, limit=limit - len(entries), offset=next_offset)
        total = page.total
        page_entries = list(page.entries)
        if not page_entries:
            break
        entries.extend(page_entries)
        next_offset += len(page_entries)
        if next_offset >= total:
            break
    return entries, total


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
