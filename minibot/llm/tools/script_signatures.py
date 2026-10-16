"""Render a tool's JSON schema as a one-line Python call signature for a model that writes scripts."""

from __future__ import annotations

import keyword
import re
from collections.abc import Mapping, Sequence
from typing import Any

_NON_IDENTIFIER = re.compile(r"[^0-9a-zA-Z_]")
_JSON_TO_PYTHON = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "object": "dict",
    "array": "list",
}
_MAX_DEPTH = 2
_MAX_ENUM_VALUES = 6
_DESCRIPTION_MAX_CHARS = 140


def python_identifier(name: str) -> str:
    """Map a tool name to a valid Python identifier: other characters become ``_``, keywords get a suffix."""
    candidate = _NON_IDENTIFIER.sub("_", name)
    if not candidate or candidate[0].isdigit():
        candidate = f"_{candidate}"
    return f"{candidate}_" if keyword.iskeyword(candidate) else candidate


def unique_identifiers(names: Sequence[str]) -> dict[str, str]:
    """Identifier per tool name; clashes after mapping get a numeric suffix, in input order."""
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for name in names:
        identifier = python_identifier(name)
        candidate, counter = identifier, 2
        while candidate in taken:
            candidate = f"{identifier}_{counter}"
            counter += 1
        taken.add(candidate)
        mapping[name] = candidate
    return mapping


def admits_null(schema: Mapping[str, Any]) -> bool:
    json_type = schema.get("type")
    if json_type == "null" or (isinstance(json_type, list) and "null" in json_type):
        return True
    return any(
        isinstance(option, Mapping) and admits_null(option)
        for key in ("anyOf", "oneOf")
        for option in (schema.get(key) or [])
    )


def render_signature(
    identifier: str,
    description: str,
    parameters: Mapping[str, Any],
    *,
    max_description_chars: int = _DESCRIPTION_MAX_CHARS,
) -> str:
    """``await tools.<identifier>(*, required: str, optional: int = None)  # first line of the description``"""
    properties = parameters.get("properties")
    required = set(parameters.get("required") or [])
    arguments: list[str] = []
    if isinstance(properties, Mapping):
        entries = [(str(key), value if isinstance(value, Mapping) else {}) for key, value in properties.items()]
        # Required arguments first, as a Python signature would list them.
        entries.sort(key=lambda entry: entry[0] not in required or admits_null(entry[1]))
        for key, schema in entries:
            hint = _type_hint(schema, 0)
            if admits_null(schema) and not hint.endswith("None"):
                hint = f"{hint} | None"
            if key in required and not admits_null(schema):
                arguments.append(f"{key}: {hint}")
            else:
                arguments.append(f"{key}: {hint} = None" if admits_null(schema) else f"{key}: {hint} = ...")
    signature = f"await tools.{identifier}({', '.join(['*', *arguments]) if arguments else ''})"
    summary = _first_line(description, max_description_chars)
    return f"{signature}  # {summary}" if summary else signature


def _first_line(text: str, limit: int) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped if len(stripped) <= limit else f"{stripped[: limit - 3]}..."
    return ""


def _type_hint(schema: Mapping[str, Any], depth: int) -> str:
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and len(enum) <= _MAX_ENUM_VALUES:
        return f"Literal[{', '.join(repr(value) for value in enum if value is not None)}]"
    json_type = schema.get("type")
    options: list[str] = []
    if isinstance(json_type, str):
        options = [json_type]
    elif isinstance(json_type, list):
        options = [item for item in json_type if isinstance(item, str)]
    else:
        for key in ("anyOf", "oneOf"):
            alternatives = schema.get(key)
            if isinstance(alternatives, list):
                hints = [_type_hint(item, depth) for item in alternatives if isinstance(item, Mapping)]
                return " | ".join(dict.fromkeys(hints)) or "Any"
    if not options:
        return "Any"
    hints = []
    for option in options:
        if option == "null":
            hints.append("None")
        elif option == "array" and depth < _MAX_DEPTH and isinstance(schema.get("items"), Mapping):
            hints.append(f"list[{_type_hint(schema['items'], depth + 1)}]")
        else:
            hints.append(_JSON_TO_PYTHON.get(option, "Any"))
    return " | ".join(dict.fromkeys(hints))
