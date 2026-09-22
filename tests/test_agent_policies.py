from __future__ import annotations

from pathlib import Path

import pytest
from llm_async.models import Tool

from minibot.adapters.config.schema import Settings
from minibot.app.agent_policies import (
    apply_agent_overrides,
    filter_tools_for_agent,
    is_retargeted,
    resolve_delegation_target,
    strip_reserved_delegation_tools,
)
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


def test_retargeting_changes_the_target_and_leaves_the_configured_cap_alone() -> None:
    """The only caller is the task worker, which loads specs from disk.

    ``max_new_tokens`` there is the cap the user wrote in the frontmatter, not one auto-config
    derived for the agent's configured model, so discarding it would throw away real intent. What
    the *target* model allows arrives separately, in the task payload.
    """
    spec = apply_agent_overrides(_retargetable_spec(), {"model_provider": "fireworks", "model": "deepseek-v4p1"})

    assert (spec.model_provider, spec.model) == ("fireworks", "deepseek-v4p1")
    assert spec.max_new_tokens == 50_000


def test_overriding_only_reasoning_effort_keeps_the_target() -> None:
    spec = apply_agent_overrides(_retargetable_spec(), {"reasoning_effort": "high"})

    assert spec.reasoning_effort == "high"
    assert (spec.model_provider, spec.model) == ("openai_responses", "gpt-5.6-luna")


def test_resolve_delegation_target_falls_back_to_the_main_llm_section() -> None:
    """An agent without `model_provider` inherits it, so a bare spec lookup resolves nothing."""
    settings = Settings.from_dict({"llm": {"provider": "openai_responses", "model": "gpt-5.6-luna"}})
    inheriting = AgentSpec(
        name="plain", description="", system_prompt="x", source_path=Path("agents/plain.md"), model=None
    )

    assert resolve_delegation_target(settings, inheriting, None) == ("openai_responses", "gpt-5.6-luna")
    # Only the model overridden: the provider still has to come from [llm], not from None.
    assert resolve_delegation_target(settings, inheriting, {"model": "deepseek-v4p1"}) == (
        "openai_responses",
        "deepseek-v4p1",
    )
    # Only the provider overridden: the model still has to resolve.
    assert resolve_delegation_target(settings, inheriting, {"model_provider": "fireworks"}) == (
        "fireworks",
        "gpt-5.6-luna",
    )
    # No spec at all is the general worker, which runs on [llm] outright.
    assert resolve_delegation_target(settings, None, {"model": "glm-5.3"}) == ("openai_responses", "glm-5.3")


def test_is_retargeted_ignores_an_override_that_repeats_the_spec() -> None:
    spec = _retargetable_spec()

    assert is_retargeted(spec, {"model": "deepseek-v4p1"}) is True
    assert is_retargeted(spec, {"model": spec.model, "model_provider": spec.model_provider}) is False
    assert is_retargeted(spec, {"reasoning_effort": "high"}) is False
    assert is_retargeted(None, {"model": "glm-5.3"}) is True
