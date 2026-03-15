from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_async.models import Tool

from minibot.adapters.lua.runtime import execute_lua_file, lua_to_python, python_to_lua
from minibot.core.agent_runtime import ToolResult
from minibot.llm.tools.base import ToolBinding, ToolContext


def load_lua_custom_tools(directory: str) -> list[ToolBinding]:
    root = Path(directory).expanduser()
    if not root.exists():
        raise ValueError(f"lua custom tools directory not found: {root}")
    if not root.is_dir():
        raise ValueError(f"lua custom tools directory is not a directory: {root}")

    bindings: list[ToolBinding] = []
    seen_names: set[str] = set()
    for path in sorted(root.glob("*.lua")):
        binding = _load_lua_tool(path)
        if binding.tool.name in seen_names:
            raise ValueError(f"duplicate lua custom tool name: {binding.tool.name}")
        seen_names.add(binding.tool.name)
        bindings.append(binding)
    return bindings


def _load_lua_tool(path: Path) -> ToolBinding:
    try:
        lua_runtime, result = execute_lua_file(path)
        manifest = lua_to_python(result)
    except Exception as exc:
        raise ValueError(f"failed to load lua tool file {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"lua tool file must return a table: {path}")

    name = manifest.get("name")
    description = manifest.get("description")
    parameters = manifest.get("parameters")
    handler = manifest.get("handler")

    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"lua tool file missing string `name`: {path}")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"lua tool file missing string `description`: {path}")
    if not isinstance(parameters, dict):
        raise ValueError(f"lua tool file must provide object `parameters`: {path}")
    if not callable(handler):
        raise ValueError(f"lua tool file must provide callable `handler`: {path}")

    tool = Tool(name=name, description=description, parameters=parameters)

    async def _handler(payload: dict[str, Any], _: ToolContext) -> ToolResult:
        try:
            raw_result = handler(python_to_lua(payload, lua_runtime))
            return ToolResult(content=lua_to_python(raw_result))
        except Exception as exc:
            raise ValueError(f"lua tool `{name}` failed from {path}: {exc}") from exc

    return ToolBinding(tool=tool, handler=_handler)
