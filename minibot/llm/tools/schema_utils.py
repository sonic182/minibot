from __future__ import annotations

from typing import Any


def strict_object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def empty_object_schema() -> dict[str, Any]:
    return strict_object(properties={}, required=[])


def nullable_string(description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": ["string", "null"]}
    if description:
        schema["description"] = description
    return schema


def string_field(description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    if description:
        schema["description"] = description
    return schema


def nullable_integer(minimum: int | None = None, description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": ["integer", "null"]}
    if minimum is not None:
        schema["minimum"] = minimum
    if description:
        schema["description"] = description
    return schema


def integer_field(minimum: int | None = None, description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "integer"}
    if minimum is not None:
        schema["minimum"] = minimum
    if description:
        schema["description"] = description
    return schema


def nullable_boolean(description: str | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": ["boolean", "null"]}
    if description:
        schema["description"] = description
    return schema


def job_id_property(description: str = "Identifier returned by schedule_prompt.") -> dict[str, Any]:
    return {"type": "string", "description": description}


def selector_entry_id_title_properties() -> dict[str, Any]:
    return {
        "entry_id": nullable_string(),
        "title": nullable_string(),
    }


def pagination_properties(*, include_active_only: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "limit": nullable_integer(minimum=1),
        "offset": nullable_integer(minimum=0),
    }
    if include_active_only:
        properties = {
            "active_only": nullable_boolean("When true, only pending/leased jobs are returned."),
            **properties,
        }
    return properties


def single_required_field_object(field_name: str, field_schema: dict[str, Any]) -> dict[str, Any]:
    return strict_object(properties={field_name: field_schema}, required=[field_name])


def attachment_array_schema() -> dict[str, Any]:
    return {
        "type": ["array", "null"],
        "items": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path under managed root"},
                "type": {"type": "string", "description": "MIME type or file type hint"},
                "caption": {"type": "string", "description": "Optional file description"},
            },
            "required": ["path", "type"],
        },
    }
