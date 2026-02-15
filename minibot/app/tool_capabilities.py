from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Sequence

from minibot.adapters.config.schema import MainAgentConfig, OrchestrationConfig
from minibot.app.agent_policies import filter_tools_for_agent
from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolBinding


@dataclass(frozen=True)
class MainAgentToolView:
    tools: list[ToolBinding]
    hidden_tool_names: list[str]


def main_agent_tool_view(
    *,
    tools: Sequence[ToolBinding],
    orchestration_config: OrchestrationConfig,
    agent_specs: Sequence[AgentSpec],
) -> MainAgentToolView:
    main_agent_tools = _apply_main_agent_policy(list(tools), orchestration_config.main_agent)
    if orchestration_config.tool_ownership_mode != "exclusive":
        return MainAgentToolView(tools=main_agent_tools, hidden_tool_names=[])

    reserved_tool_names: set[str] = set()
    for spec in agent_specs:
        for binding in filter_tools_for_agent(main_agent_tools, spec):
            reserved_tool_names.add(binding.tool.name)

    visible_tools = [binding for binding in main_agent_tools if binding.tool.name not in reserved_tool_names]
    return MainAgentToolView(tools=visible_tools, hidden_tool_names=sorted(reserved_tool_names))


def summarize_agent_capabilities(spec: AgentSpec) -> str:
    capability_hints: list[str] = []
    if spec.mcp_servers:
        capability_hints.append(f"mcp_servers={','.join(spec.mcp_servers)}")
    if spec.tools_allow:
        capability_hints.append(f"tools_allow={','.join(spec.tools_allow)}")
    if spec.tools_deny:
        capability_hints.append(f"tools_deny={','.join(spec.tools_deny)}")
    if not capability_hints:
        return spec.description
    if spec.description:
        return f"{spec.description} ({'; '.join(capability_hints)})"
    return "; ".join(capability_hints)


def _apply_main_agent_policy(tools: list[ToolBinding], main_agent: MainAgentConfig) -> list[ToolBinding]:
    allow_patterns = [item.strip() for item in main_agent.tools_allow if item.strip()]
    deny_patterns = [item.strip() for item in main_agent.tools_deny if item.strip()]
    if allow_patterns:
        return [binding for binding in tools if _matches_any(binding.tool.name, allow_patterns)]
    if deny_patterns:
        return [binding for binding in tools if not _matches_any(binding.tool.name, deny_patterns)]
    return tools


def _matches_any(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch(name, pattern) for pattern in patterns)
