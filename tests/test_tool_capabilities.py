from __future__ import annotations

from pathlib import Path

from llm_async.models import Tool

from minibot.adapters.config.schema import MainAgentConfig, OrchestrationConfig
from minibot.app.tool_capabilities import main_agent_tool_view, summarize_agent_capabilities
from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolBinding


async def _noop_handler(*_args, **_kwargs):
    return {"ok": True}


def _binding(name: str) -> ToolBinding:
    return ToolBinding(
        tool=Tool(name=name, description=name, parameters={"type": "object", "properties": {}, "required": []}),
        handler=_noop_handler,
    )


def test_main_agent_tool_view_exclusive_hides_agent_owned_tools() -> None:
    tools = [_binding("current_datetime"), _binding("calculate_expression")]
    spec = AgentSpec(
        name="worker",
        description="worker",
        system_prompt="worker",
        source_path=Path("worker.md"),
        tools_allow=["calculate_*"],
    )
    config = OrchestrationConfig(
        tool_ownership_mode="exclusive",
        main_agent=MainAgentConfig(tools_allow=["current_*", "calculate_*"]),
    )

    view = main_agent_tool_view(tools=tools, orchestration_config=config, agent_specs=[spec])

    assert [binding.tool.name for binding in view.tools] == ["current_datetime"]
    assert view.hidden_tool_names == ["calculate_expression"]


def test_summarize_agent_capabilities_includes_tool_hints() -> None:
    spec = AgentSpec(
        name="browser",
        description="browser specialist",
        system_prompt="browser",
        source_path=Path("browser.md"),
        mcp_servers=["playwright-cli"],
        tools_allow=["mcp_playwright-cli__*"],
    )

    summary = summarize_agent_capabilities(spec)

    assert "mcp_servers=playwright-cli" in summary
    assert "tools_allow=mcp_playwright-cli__*" in summary
