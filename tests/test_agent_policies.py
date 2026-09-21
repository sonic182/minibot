from __future__ import annotations

from pathlib import Path

import pytest
from llm_async.models import Tool

from minibot.app.agent_policies import (
    apply_agent_overrides,
    filter_tools_for_agent,
    strip_reserved_delegation_tools,
)
from minibot.app.token_limits_autoconfig import prime_model_limits
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


def test_strip_reserved_delegation_tools_removes_recursive_tools() -> None:
    tools = [
        _binding("fetch_agent_info"),
        _binding("spawn_task"),
        _binding("cancel_task"),
        _binding("list_tasks"),
        _binding("current_datetime"),
    ]

    filtered = strip_reserved_delegation_tools(tools)
    names = [binding.tool.name for binding in filtered]

    assert names == ["current_datetime"]


def _retargetable_spec() -> AgentSpec:
    return AgentSpec(
        name="browser",
        description="browser specialist",
        system_prompt="drive the browser",
        source_path=Path("agents/browser_agent.md"),
        model_provider="openai_responses",
        model="gpt-5.6-luna",
        context_limit=1_050_000,
        max_new_tokens=50_000,
    )


def test_retargeting_rederives_the_caps_from_the_target_model() -> None:
    prime_model_limits(
        "fireworks", "deepseek-v4p1", {"catalog_provider": "fireworks", "context": 163840, "output": 16384}
    )

    spec = apply_agent_overrides(
        _retargetable_spec(),
        {"model_provider": "fireworks", "model": "deepseek-v4p1"},
        context_ratio=0.95,
    )

    # Not the 1.05M of the spec's own model: inheriting that would compact ~6x too late.
    assert spec.context_limit == 163840
    assert spec.max_new_tokens == 16384


def test_retargeting_without_cached_limits_drops_the_caps() -> None:
    spec = apply_agent_overrides(
        _retargetable_spec(),
        {"model_provider": "fireworks", "model": "never-seen"},
        context_ratio=0.95,
    )

    assert spec.context_limit is None
    assert spec.max_new_tokens is None


def test_overriding_only_reasoning_effort_keeps_the_boot_derived_caps() -> None:
    spec = apply_agent_overrides(_retargetable_spec(), {"reasoning_effort": "high"}, context_ratio=0.95)

    assert spec.reasoning_effort == "high"
    assert spec.context_limit == 1_050_000
    assert spec.max_new_tokens == 50_000
