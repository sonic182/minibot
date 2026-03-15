from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


def create_lua_runtime():
    try:
        lupa = importlib.import_module("lupa")
    except ModuleNotFoundError as exc:
        raise RuntimeError("Lua support requires `lupa`; install with `poetry install --extras lua`") from exc
    return lupa.LuaRuntime(unpack_returned_tuples=True)


def execute_lua_file(path: Path):
    lua = create_lua_runtime()
    result = lua.execute(path.read_text(encoding="utf-8"))
    return lua, result


def lua_to_python(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, tuple):
        raise ValueError("lua values must return a single value")
    if isinstance(value, list):
        return [lua_to_python(item) for item in value]
    if isinstance(value, dict):
        return {key: lua_to_python(item) for key, item in value.items()}
    if not hasattr(value, "items"):
        return value

    items = [(key, item) for key, item in value.items()]
    if not items:
        return {}

    keys = [key for key, _ in items]
    if all(isinstance(key, str) for key in keys):
        return {key: lua_to_python(item) for key, item in items}

    if all(isinstance(key, int) and not isinstance(key, bool) and key > 0 for key in keys):
        ordered_keys = sorted(keys)
        expected_keys = list(range(1, len(keys) + 1))
        if ordered_keys != expected_keys:
            raise ValueError("lua arrays must use consecutive integer keys starting at 1")
        ordered_items = sorted(items, key=lambda item: item[0])
        return [lua_to_python(item) for _, item in ordered_items]

    raise ValueError("lua tables must use either string keys or consecutive integer keys")


def python_to_lua(value: Any, lua_runtime: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        table = lua_runtime.table()
        for index, item in enumerate(value, start=1):
            table[index] = python_to_lua(item, lua_runtime)
        return table
    if isinstance(value, dict):
        table = lua_runtime.table()
        for key, item in value.items():
            table[key] = python_to_lua(item, lua_runtime)
        return table
    raise ValueError(f"unsupported Python value for Lua conversion: {type(value).__name__}")
