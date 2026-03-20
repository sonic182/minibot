from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from minibot.adapters.config.schema import Settings


_INDENT = "  "
_LUA_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LUA_RESERVED_WORDS = {
    "and",
    "break",
    "do",
    "else",
    "elseif",
    "end",
    "false",
    "for",
    "function",
    "goto",
    "if",
    "in",
    "local",
    "nil",
    "not",
    "or",
    "repeat",
    "return",
    "then",
    "true",
    "until",
    "while",
}


def settings_to_lua_text(settings: Settings) -> str:
    payload = settings.model_dump(mode="python", exclude_none=True)
    return "return " + _render_value(payload, level=0) + "\n"


def convert_toml_to_lua_file(input_path: Path, output_path: Path) -> None:
    if input_path.suffix.lower() != ".toml":
        raise ValueError("input config must use the .toml extension")
    settings = Settings.from_file(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(settings_to_lua_text(settings), encoding="utf-8")


def _render_value(value: Any, *, level: int) -> str:
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("cannot serialize non-finite float values to Lua")
        return repr(value)
    if isinstance(value, str):
        return _quote_lua_string(value)
    if isinstance(value, list):
        return _render_list(value, level=level)
    if isinstance(value, dict):
        return _render_dict(value, level=level)
    raise ValueError(f"unsupported value type for Lua serialization: {type(value).__name__}")


def _render_list(values: list[Any], *, level: int) -> str:
    if not values:
        return "{}"
    indent = _INDENT * (level + 1)
    closing_indent = _INDENT * level
    rendered_items = [f"{indent}{_render_value(item, level=level + 1)}," for item in values]
    return "{\n" + "\n".join(rendered_items) + f"\n{closing_indent}" + "}"


def _render_dict(values: dict[Any, Any], *, level: int) -> str:
    if not values:
        return "{}"
    indent = _INDENT * (level + 1)
    closing_indent = _INDENT * level
    rendered_items = []
    for key in sorted(values):
        if not isinstance(key, str):
            raise ValueError("Lua serialization only supports string-keyed dictionaries")
        rendered_key = key if _LUA_IDENTIFIER_RE.match(key) and key not in _LUA_RESERVED_WORDS else (
            f"[{_quote_lua_string(key)}]"
        )
        rendered_value = _render_value(values[key], level=level + 1)
        rendered_items.append(f"{indent}{rendered_key} = {rendered_value},")
    return "{\n" + "\n".join(rendered_items) + f"\n{closing_indent}" + "}"


def _quote_lua_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace('"', '\\"')
    )
    return f'"{escaped}"'
