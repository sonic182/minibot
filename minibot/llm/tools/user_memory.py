"""Long-term user-memory tools backed by :class:`KeyValueMemory`, one tool per operation."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from llm_async.models import Tool

from minibot.core.memory import KeyValueEntry, KeyValueMemory, KeyValueMemoryFilter
from minibot.llm.tools.arg_utils import optional_int, optional_str, require_owner
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_string, pagination_properties, strict_object
from minibot.shared.datetime_utils import parse_optional_iso_datetime_utc
from minibot.shared.errors import ToolInputError

MEMORY_CATEGORIES = (
    "finanzas",
    "recordatorios",
    "proyectos",
    "preferencias",
    "salud",
    "viajes",
    "vehículos",
    "contactos",
    "seguimiento",
    "conocimiento",
    "otros",
)


def build_kv_tools(memory: KeyValueMemory) -> list[ToolBinding]:
    return [
        ToolBinding(tool=_create_tool(), handler=lambda payload, ctx: _create_entry(memory, payload, ctx)),
        ToolBinding(tool=_update_tool(), handler=lambda payload, ctx: _update_entry(memory, payload, ctx)),
        ToolBinding(tool=_get_tool(), handler=lambda payload, ctx: _get_entry(memory, payload, ctx)),
        ToolBinding(tool=_search_tool(), handler=lambda payload, ctx: _search_entries(memory, payload, ctx)),
        ToolBinding(tool=_delete_tool(), handler=lambda payload, ctx: _delete_entry(memory, payload, ctx)),
        ToolBinding(tool=_list_titles_tool(), handler=lambda payload, ctx: _list_titles(memory, payload, ctx)),
    ]


def _create_tool() -> Tool:
    return Tool(
        name="memory_create",
        description=load_tool_description("memory_create"),
        parameters=strict_object(
            properties={
                "title": nullable_string("Title of the new entry. Must be unique."),
                "data": nullable_string("Entry content."),
                "category": {"type": "string", "enum": list(MEMORY_CATEGORIES), "description": "Required category."},
                "metadata": nullable_string(
                    "Optional JSON object with extra structured context; category is separate."
                ),
                "source": nullable_string("Optional source of the fact."),
                "expires_at": nullable_string("Optional ISO datetime expiry."),
            },
            required=["title", "data", "category", "metadata", "source", "expires_at"],
        ),
    )


def _update_tool() -> Tool:
    return Tool(
        name="memory_update",
        description=load_tool_description("memory_update"),
        parameters=strict_object(
            properties={
                "entry_id": nullable_string("Id of the entry to update, from memory_search or memory_list_titles."),
                "data": nullable_string("New entry content."),
                "category": _nullable_category_schema(),
                "metadata": nullable_string("Optional JSON object with extra structured context."),
                "source": nullable_string("Optional source of the fact."),
                "expires_at": nullable_string("Optional ISO datetime expiry."),
            },
            required=["entry_id", "data", "category", "metadata", "source", "expires_at"],
        ),
    )


def _get_tool() -> Tool:
    return Tool(
        name="memory_get",
        description=load_tool_description("memory_get"),
        parameters=strict_object(
            properties={"entry_id": nullable_string("Id of the entry to read.")},
            required=["entry_id"],
        ),
    )


def _search_tool() -> Tool:
    return Tool(
        name="memory_search",
        description=load_tool_description("memory_search"),
        parameters=strict_object(
            properties={
                "query": nullable_string("Text to match against titles and data."),
                "category": _nullable_category_schema(),
                "source": nullable_string("Only entries from this source."),
                "updated_after": nullable_string("Inclusive ISO datetime lower bound on updated_at."),
                "updated_before": nullable_string("Inclusive ISO datetime upper bound on updated_at."),
                **pagination_properties(),
            },
            required=["query", "category", "source", "updated_after", "updated_before", "limit", "offset"],
        ),
    )


def _delete_tool() -> Tool:
    return Tool(
        name="memory_delete",
        description=load_tool_description("memory_delete"),
        parameters=strict_object(
            properties={"entry_id": nullable_string("Id of the entry to delete.")},
            required=["entry_id"],
        ),
    )


def _list_titles_tool() -> Tool:
    return Tool(
        name="memory_list_titles",
        description=load_tool_description("memory_list_titles"),
        parameters=strict_object(
            properties={
                "category": _nullable_category_schema(),
                "source": nullable_string("Only entries from this source."),
                "updated_after": nullable_string("Inclusive ISO datetime lower bound on updated_at."),
                "updated_before": nullable_string("Inclusive ISO datetime upper bound on updated_at."),
                **pagination_properties(),
            },
            required=["category", "source", "updated_after", "updated_before", "limit", "offset"],
        ),
    )


def _nullable_category_schema() -> dict[str, Any]:
    return {"anyOf": [{"type": "string", "enum": list(MEMORY_CATEGORIES)}, {"type": "null"}]}


async def _create_entry(memory: KeyValueMemory, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    owner_id = require_owner(context)
    metadata = _metadata_with_category(_coerce_metadata(payload.get("metadata")), _require_category(payload))
    result = await memory.create_entry(
        owner_id=owner_id,
        title=_require_create_text(payload, "title"),
        data=_require_create_text(payload, "data"),
        metadata=metadata,
        source=optional_str(payload.get("source")),
        expires_at=_parse_datetime(payload.get("expires_at"), field="expires_at"),
    )
    entry_payload = _entry_payload(result.entry)
    if result.created:
        return {"created": True, **entry_payload}
    return {
        "ok": False,
        "error": "An entry with this title already exists. Use memory_update with its entry_id instead.",
        "error_code": "memory:create:duplicate_title",
        "existing_entry": entry_payload,
    }


async def _update_entry(memory: KeyValueMemory, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    owner_id = require_owner(context)
    entry_id = _require_entry_id(payload, "memory_update")
    data = optional_str(payload.get("data"))
    metadata = _coerce_metadata(payload.get("metadata"))
    category = _optional_category(payload.get("category"))
    if metadata is not None and "category" in metadata:
        raise _category_in_metadata_error()
    if category:
        metadata = _metadata_with_category(metadata, category)
    source = optional_str(payload.get("source"))
    expires_at = _parse_datetime(payload.get("expires_at"), field="expires_at")
    if data is None and metadata is None and source is None and expires_at is None:
        raise _input_error(
            "nothing_to_update", "update requires at least one of data, category, metadata, source or expires_at."
        )
    entry = await memory.update_entry(
        owner_id=owner_id,
        entry_id=entry_id,
        data=data,
        metadata=metadata,
        source=source,
        expires_at=expires_at,
    )
    if entry is None:
        return {"ok": False, "error": "Entry not found", "error_code": "memory:update:not_found", "entry_id": entry_id}
    return {"updated": True, **_entry_payload(entry)}


async def _get_entry(memory: KeyValueMemory, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    owner_id = require_owner(context)
    entry_id = _require_entry_id(payload, "memory_get")
    entry = await memory.get_entry(owner_id=owner_id, entry_id=entry_id)
    if entry is None:
        return {"ok": False, "error": "Entry not found", "entry_id": entry_id}
    return _entry_payload(entry)


async def _search_entries(memory: KeyValueMemory, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    owner_id = require_owner(context)
    result = await memory.search_entries(
        owner_id=owner_id,
        query=optional_str(payload.get("query")),
        filters=_parse_filters(payload),
        limit=_optional_limit(payload),
        offset=_optional_offset(payload),
    )
    return _search_payload(owner_id, result)


async def _delete_entry(memory: KeyValueMemory, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    owner_id = require_owner(context)
    entry_id = _require_entry_id(payload, "memory_delete")
    deleted = await memory.delete_entry(owner_id=owner_id, entry_id=entry_id)
    return {"owner_id": owner_id, "deleted": deleted, "entry_id": entry_id}


async def _list_titles(memory: KeyValueMemory, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    owner_id = require_owner(context)
    result = await memory.list_entries(
        owner_id=owner_id,
        filters=_parse_filters(payload),
        limit=_optional_limit(payload),
        offset=_optional_offset(payload),
    )
    titles = [
        {
            "id": entry.id,
            "title": entry.title,
            "category": entry.metadata.get("category"),
            "updated_at": entry.updated_at.isoformat(),
            "source": entry.source,
        }
        for entry in result.entries
    ]
    response: dict[str, Any] = {
        "owner_id": owner_id,
        "total": result.total,
        "limit": result.limit,
        "offset": result.offset,
        "titles": titles,
    }
    if not titles:
        response["hint"] = "No memory entries found. Do not retry with identical parameters."
    return response


def _parse_filters(payload: dict[str, Any]) -> KeyValueMemoryFilter:
    return KeyValueMemoryFilter(
        category=_optional_category(payload.get("category")),
        source=optional_str(payload.get("source")),
        updated_after=_parse_datetime(payload.get("updated_after"), field="updated_after"),
        updated_before=_parse_datetime(payload.get("updated_before"), field="updated_before"),
    )


def _optional_limit(payload: dict[str, Any]) -> int | None:
    return optional_int(
        payload.get("limit"),
        field="limit",
        allow_float=True,
        allow_string=True,
        reject_bool=False,
        type_error="Expected integer value",
    )


def _optional_offset(payload: dict[str, Any]) -> int | None:
    return optional_int(
        payload.get("offset"),
        field="offset",
        allow_float=True,
        allow_string=True,
        reject_bool=False,
        type_error="Expected integer value",
    )


def _search_payload(owner_id: str, result: Any) -> dict[str, Any]:
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
        "category": entry.metadata.get("category"),
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
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise _invalid_metadata_error() from exc
        if not isinstance(parsed, dict):
            raise _invalid_metadata_error()
        return parsed
    raise _invalid_metadata_error()


def _metadata_with_category(metadata: dict[str, Any] | None, category: str) -> dict[str, Any]:
    if metadata is not None and "category" in metadata:
        raise _category_in_metadata_error()
    return {**(metadata or {}), "category": category}


def _require_category(payload: dict[str, Any]) -> str:
    category = _optional_category(payload.get("category"))
    if category is None:
        raise _input_error(
            "category_required",
            f"category is required for create. Use exactly one of: {', '.join(MEMORY_CATEGORIES)}.",
        )
    return category


def _optional_category(value: Any) -> str | None:
    category = optional_str(value)
    if category is None:
        return None
    if category not in MEMORY_CATEGORIES:
        raise _input_error(
            "invalid_category",
            f"category {category!r} is not valid. Use exactly one of: {', '.join(MEMORY_CATEGORIES)}.",
        )
    return category


def _input_error(code: str, message: str) -> ToolInputError:
    return ToolInputError(message, error_code=f"memory:invalid_arguments:{code}")


def _require_create_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _input_error(f"{field}_required", f"{field} is required for create and must be a non-empty string.")
    return value.strip()


def _require_entry_id(payload: dict[str, Any], tool_name: str) -> str:
    entry_id = payload.get("entry_id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise _input_error(
            "entry_id_required",
            f"{tool_name} requires entry_id. Get it from memory_search or memory_list_titles; "
            "to add a new entry use memory_create instead.",
        )
    return entry_id.strip()


def _category_in_metadata_error() -> ToolInputError:
    return _input_error(
        "category_in_metadata",
        "metadata must not contain category: pass it as the top-level category field and remove it from metadata.",
    )


def _invalid_metadata_error() -> ToolInputError:
    return _input_error("invalid_metadata", 'metadata must be a JSON object, for example {"note": "text"}.')


def _parse_datetime(value: Any, *, field: str) -> datetime | None:
    return parse_optional_iso_datetime_utc(value, field=field)
