from __future__ import annotations

from fnmatch import fnmatch
from typing import Sequence

from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolBinding


def filter_tools_for_agent(tools: Sequence[ToolBinding], spec: AgentSpec) -> list[ToolBinding]:
    if spec.tools_allow and spec.tools_deny:
        raise ValueError("agent tool policy cannot define both tools_allow and tools_deny")

    allow_mode = bool(spec.tools_allow)
    deny_mode = bool(spec.tools_deny)
    mcp_servers = {item.strip() for item in spec.mcp_servers if item.strip()}
    filtered: list[ToolBinding] = []
    for binding in tools:
        tool_name = binding.tool.name
        is_mcp = _is_mcp_tool(tool_name)
        if is_mcp:
            server = _extract_mcp_server(tool_name)
            if server is None or server not in mcp_servers:
                continue
            if deny_mode and any(_matches(tool_name, pattern) for pattern in spec.tools_deny):
                continue
            filtered.append(binding)
            continue

        if allow_mode:
            if any(_matches(tool_name, pattern) for pattern in spec.tools_allow):
                filtered.append(binding)
            continue
        if deny_mode:
            if not any(_matches(tool_name, pattern) for pattern in spec.tools_deny):
                filtered.append(binding)
            continue
        # Neither allow nor deny: no non-MCP tools are exposed.
    return filtered


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
