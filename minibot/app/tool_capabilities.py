from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from minibot.app.agent_policies import filter_tools_for_agent
from minibot.app.extensions import DerivedTools
from minibot.app.mcp_tool_name import DEFAULT_MCP_NAME_PREFIX, extract_mcp_server, is_mcp_tool_name
from minibot.app.tool_policy_utils import apply_allow_deny, normalize_patterns
from minibot.config.schema import MainAgentConfig, OrchestrationConfig
from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolBinding


@dataclass(frozen=True)
class MainAgentToolView:
    """The main agent's tools. ``replaced_tools`` were taken over by a derived tool (codemode): they are not
    offered to the model but stay usable through it."""

    tools: list[ToolBinding]
    hidden_tool_names: list[str]
    replaced_tools: list[ToolBinding] = field(default_factory=list)


def main_agent_tool_view(
    *,
    tools: Sequence[ToolBinding],
    orchestration_config: OrchestrationConfig,
    agent_specs: Sequence[AgentSpec],
    mcp_name_prefix: str = DEFAULT_MCP_NAME_PREFIX,
    derive_tools: Callable[[Sequence[ToolBinding]], DerivedTools] | None = None,
) -> MainAgentToolView:
    """The tools the main agent sees, after ``main_agent`` allow/deny and exclusive ownership.

    ``derive_tools`` builds extra tools from that final list (see ``ExtensionContext.add_tool_deriver``);
    they are subject to the same ``main_agent`` allow/deny policy and are never hidden by ownership. A derived
    tool the policy keeps hides the tools it replaces.
    """
    main_agent_tools = _apply_main_agent_policy(list(tools), orchestration_config.main_agent)
    view = _apply_tool_ownership(main_agent_tools, orchestration_config, agent_specs, mcp_name_prefix)
    if derive_tools is None:
        return view
    derived = derive_tools(view.tools)
    visible = _apply_main_agent_policy(derived.tools, orchestration_config.main_agent)
    replaced = derived.replaced_names(visible)
    return MainAgentToolView(
        tools=derived.merge(view.tools, visible),
        hidden_tool_names=view.hidden_tool_names,
        replaced_tools=[binding for binding in view.tools if binding.tool.name in replaced],
    )


def _apply_tool_ownership(
    main_agent_tools: list[ToolBinding],
    orchestration_config: OrchestrationConfig,
    agent_specs: Sequence[AgentSpec],
    mcp_name_prefix: str,
) -> MainAgentToolView:
    if orchestration_config.tool_ownership_mode not in {"exclusive", "exclusive_mcp"}:
        return MainAgentToolView(tools=main_agent_tools, hidden_tool_names=[])

    shared_servers = {name.strip() for name in orchestration_config.shared_mcp_servers if name.strip()}
    reserved_tool_names: set[str] = set()
    for spec in agent_specs:
        for binding in filter_tools_for_agent(main_agent_tools, spec, mcp_name_prefix=mcp_name_prefix):
            tool_name = binding.tool.name
            if orchestration_config.tool_ownership_mode == "exclusive_mcp" and not is_mcp_tool_name(
                tool_name, prefix=mcp_name_prefix
            ):
                continue
            if extract_mcp_server(tool_name, prefix=mcp_name_prefix) in shared_servers:
                continue
            reserved_tool_names.add(tool_name)

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
    return apply_allow_deny(
        tools,
        name_of=lambda binding: binding.tool.name,
        allow_patterns=normalize_patterns(main_agent.tools_allow),
        deny_patterns=normalize_patterns(main_agent.tools_deny),
    )
