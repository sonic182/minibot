from __future__ import annotations

from pathlib import Path

from llm_async.models import Tool
import pytest

from minibot.app.agent_policies import filter_tools_for_agent
from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolBinding


async def _noop_handler(*_args, **_kwargs):
    return {"ok": True}


def _binding(name: str) -> ToolBinding:
    return ToolBinding(
        tool=Tool(name=name, description=name, parameters={"type": "object", "properties": {}, "required": []}),
        handler=_noop_handler,
    )


def _spec(
    *,
    mcp_servers: list[str],
    tools_allow: list[str] | None = None,
    tools_deny: list[str] | None = None,
) -> AgentSpec:
    return AgentSpec(
        name="test_agent",
        description="test",
        system_prompt="you are test",
        source_path=Path("/tmp/test_agent.md"),
        mcp_servers=mcp_servers,
        tools_allow=tools_allow or [],
        tools_deny=tools_deny or [],
    )


def test_agent_policy_allows_all_tools_from_allowed_mcp_server() -> None:
    tools = [
        _binding("mcp_playwright-cli__browser_navigate"),
        _binding("mcp_playwright-cli__browser_click"),
        _binding("mcp_other__tool"),
        _binding("current_datetime"),
    ]
    spec = _spec(mcp_servers=["playwright-cli"])

    filtered = filter_tools_for_agent(tools, spec)
    names = [binding.tool.name for binding in filtered]

    assert "mcp_playwright-cli__browser_navigate" in names
    assert "mcp_playwright-cli__browser_click" in names
    assert "mcp_other__tool" not in names
    assert "current_datetime" not in names


def test_agent_policy_tool_allow_includes_allowed_local_tools_and_allowed_mcp_servers() -> None:
    tools = [
        _binding("mcp_playwright-cli__browser_navigate"),
        _binding("mcp_playwright-cli__browser_click"),
        _binding("mcp_other__tool"),
        _binding("current_datetime"),
    ]
    spec = _spec(mcp_servers=["playwright-cli"], tools_allow=["current_*"])

    filtered = filter_tools_for_agent(tools, spec)
    names = [binding.tool.name for binding in filtered]

    assert "current_datetime" in names
    assert "mcp_playwright-cli__browser_navigate" in names
    assert "mcp_playwright-cli__browser_click" in names
    assert "mcp_other__tool" not in names


def test_agent_policy_tool_deny_keeps_other_local_tools_and_allowed_mcp_servers() -> None:
    tools = [
        _binding("mcp_playwright-cli__browser_navigate"),
        _binding("mcp_other__tool"),
        _binding("current_datetime"),
        _binding("calculate_expression"),
    ]
    spec = _spec(mcp_servers=["playwright-cli"], tools_deny=["calculate_*"])

    filtered = filter_tools_for_agent(tools, spec)
    names = [binding.tool.name for binding in filtered]

    assert "current_datetime" in names
    assert "calculate_expression" not in names
    assert "mcp_playwright-cli__browser_navigate" in names
    assert "mcp_other__tool" not in names


def test_agent_policy_without_allow_or_deny_keeps_only_allowed_mcp_servers() -> None:
    tools = [
        _binding("mcp_playwright-cli__browser_navigate"),
        _binding("current_datetime"),
    ]
    spec = _spec(mcp_servers=["playwright-cli"])

    filtered = filter_tools_for_agent(tools, spec)
    names = [binding.tool.name for binding in filtered]

    assert names == ["mcp_playwright-cli__browser_navigate"]


def test_agent_policy_rejects_allow_and_deny_together() -> None:
    tools = [_binding("current_datetime")]
    spec = _spec(mcp_servers=[], tools_allow=["current_*"], tools_deny=["current_datetime"])

    with pytest.raises(ValueError):
        filter_tools_for_agent(tools, spec)
