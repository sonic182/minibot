from __future__ import annotations

from typing import Sequence

from minibot.app.mcp_tool_name import extract_mcp_server, is_mcp_tool_name
from minibot.app.tool_policy_utils import matches_any, normalize_patterns, validate_allow_deny
from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolBinding


def filter_tools_for_agent(tools: Sequence[ToolBinding], spec: AgentSpec) -> list[ToolBinding]:
    validate_allow_deny(spec.tools_allow, spec.tools_deny)

    allow_patterns = normalize_patterns(spec.tools_allow)
    deny_patterns = normalize_patterns(spec.tools_deny)
    allow_mode = bool(allow_patterns)
    deny_mode = bool(deny_patterns)
    mcp_servers = {item.strip() for item in spec.mcp_servers if item.strip()}
    filtered: list[ToolBinding] = []
    for binding in tools:
        tool_name = binding.tool.name
        is_mcp = is_mcp_tool_name(tool_name)
        if is_mcp:
            server = extract_mcp_server(tool_name)
            if server is None or server not in mcp_servers:
                continue
            if deny_mode and matches_any(tool_name, deny_patterns):
                continue
            filtered.append(binding)
            continue

        if allow_mode:
            if matches_any(tool_name, allow_patterns):
                filtered.append(binding)
            continue
        if deny_mode:
            if not matches_any(tool_name, deny_patterns):
                filtered.append(binding)
            continue
        # Neither allow nor deny: no non-MCP tools are exposed.
    return filtered
