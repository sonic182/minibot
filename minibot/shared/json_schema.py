from __future__ import annotations

from typing import Any


def to_openai_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    normalized = _normalize_schema(schema)
    if not isinstance(normalized, dict):
        raise ValueError("schema must normalize to an object")
    return normalized


def _normalize_schema(node: Any) -> Any:
    if isinstance(node, list):
        return [_normalize_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    original_required = _required_set(node.get("required"))
    normalized: dict[str, Any] = {key: _normalize_schema(value) for key, value in node.items()}

    properties = normalized.get("properties")
    is_object_schema = normalized.get("type") == "object" or isinstance(properties, dict)
    if is_object_schema:
        if not isinstance(properties, dict):
            properties = {}
            normalized["properties"] = properties

        property_names = [name for name in properties if isinstance(name, str)]
        for name in property_names:
            if name not in original_required:
                properties[name] = _ensure_nullable_schema(properties[name])
        normalized["required"] = property_names
        normalized["additionalProperties"] = False

    return normalized


def _required_set(required: Any) -> set[str]:
    if not isinstance(required, list):
        return set()
    return {item for item in required if isinstance(item, str)}


def _ensure_nullable_schema(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema

    type_value = schema.get("type")
    if isinstance(type_value, str):
        if type_value == "null":
            return schema
        updated = dict(schema)
        updated["type"] = [type_value, "null"]
        return updated
    if isinstance(type_value, list):
        if "null" in type_value:
            return schema
        updated = dict(schema)
        updated["type"] = [*type_value, "null"]
        return updated

    for combinator in ("anyOf", "oneOf"):
        options = schema.get(combinator)
        if isinstance(options, list):
            if _has_null_option(options):
                return schema
            updated = dict(schema)
            updated[combinator] = [*options, {"type": "null"}]
            return updated

    return {"anyOf": [schema, {"type": "null"}]}


def _has_null_option(options: list[Any]) -> bool:
    for option in options:
        if not isinstance(option, dict):
            continue
        option_type = option.get("type")
        if option_type == "null":
            return True
        if isinstance(option_type, list) and "null" in option_type:
            return True
    return False
