from __future__ import annotations

from fnmatch import fnmatch
from typing import Sequence

from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolBinding


_EDIT_TOOL_NAMES = {"create_file", "move_file", "delete_file"}
_WRITE_TOOL_NAMES = {"create_file", "move_file", "delete_file", "python_execute"}
_BASH_TOOL_NAMES = {"python_execute"}


def filter_tools_for_agent(tools: Sequence[ToolBinding], spec: AgentSpec) -> list[ToolBinding]:
    filtered: list[ToolBinding] = []
    for binding in tools:
        tool_name = binding.tool.name
        if not _capability_allowed(tool_name, spec):
            continue
        if spec.mcp_servers and _is_mcp_tool(tool_name):
            server = _extract_mcp_server(tool_name)
            if server is None or server not in spec.mcp_servers:
                continue
        if spec.tool_allow and not any(_matches(tool_name, pattern) for pattern in spec.tool_allow):
            continue
        if spec.tool_deny and any(_matches(tool_name, pattern) for pattern in spec.tool_deny):
            continue
        filtered.append(binding)
    return filtered


def _capability_allowed(tool_name: str, spec: AgentSpec) -> bool:
    if not spec.allow_edit and tool_name in _EDIT_TOOL_NAMES:
        return False
    if not spec.allow_write and tool_name in _WRITE_TOOL_NAMES:
        return False
    if not spec.allow_bash and tool_name in _BASH_TOOL_NAMES:
        return False
    return True


def _matches(name: str, pattern: str) -> bool:
    candidate = pattern.strip()
    if not candidate:
        return False
    return fnmatch(name, candidate)


def _is_mcp_tool(tool_name: str) -> bool:
    return tool_name.startswith("mcp_") and "__" in tool_name


def _extract_mcp_server(tool_name: str) -> str | None:
    if not _is_mcp_tool(tool_name):
        return None
    return tool_name[len("mcp_") :].split("__", 1)[0]
