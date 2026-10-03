from __future__ import annotations

from pathlib import Path

import pytest

from minibot.app.managed_agent_policy import (
    MANAGEMENT_TOOL_NAMES,
    ManagedAgentPolicy,
    is_managed_definition,
)
from minibot.config.schema import Settings
from minibot.core.agents import AgentSpec


def _spec(**overrides: object) -> AgentSpec:
    payload: dict[str, object] = {
        "name": "helper_agent",
        "description": "helper",
        "system_prompt": "help",
        "source_path": Path("data/agents/helper_agent.md"),
    }
    payload.update(overrides)
    return AgentSpec(**payload)  # type: ignore[arg-type]


def _policy(**management: object) -> ManagedAgentPolicy:
    settings = Settings.from_dict({"orchestration": {"agent_management": {"write": True, **management}}})
    return ManagedAgentPolicy.from_settings(settings)


def test_an_empty_ceiling_grants_nothing() -> None:
    with pytest.raises(ValueError, match="is not allowed by"):
        _policy().authorize(_spec(tools_allow=["bash"]))


def test_a_listed_tool_is_allowed() -> None:
    _policy(tools_allow=["filesystem", "read_file"]).authorize(_spec(tools_allow=["filesystem"]))


def test_the_ceiling_may_use_a_wildcard() -> None:
    _policy(tools_allow=["file*"]).authorize(_spec(tools_allow=["filesystem"]))


def test_a_pattern_in_the_managed_grant_is_rejected() -> None:
    with pytest.raises(ValueError, match="is a pattern"):
        _policy(tools_allow=["filesystem"]).authorize(_spec(tools_allow=["file*"]))


def test_deny_only_is_rejected() -> None:
    with pytest.raises(ValueError, match="must use tools_allow, not tools_deny"):
        _policy(tools_allow=["filesystem"]).authorize(_spec(tools_deny=["bash"]))


def test_an_mcp_tool_name_belongs_in_mcp_servers() -> None:
    with pytest.raises(ValueError, match="claims MCP servers through mcp_servers"):
        _policy(tools_allow=["mcp_search__query"]).authorize(_spec(tools_allow=["mcp_search__query"]))


@pytest.mark.parametrize("name", MANAGEMENT_TOOL_NAMES)
def test_management_tools_are_reserved(name: str) -> None:
    with pytest.raises(ValueError, match="is reserved"):
        _policy(tools_allow=[name]).authorize(_spec(tools_allow=[name]))


@pytest.mark.parametrize("name", ["spawn_task", "fetch_agent_info", "cancel_task", "list_tasks", "get_task"])
def test_delegation_tools_are_reserved(name: str) -> None:
    with pytest.raises(ValueError, match="is reserved"):
        _policy(tools_allow=[name]).authorize(_spec(tools_allow=[name]))


def test_an_unlisted_mcp_server_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"is not in \[orchestration\.agent_management\]\.mcp_servers"):
        _policy().authorize(_spec(mcp_servers=["playwright"]))


def test_a_listed_mcp_server_is_allowed() -> None:
    _policy(mcp_servers=["playwright"]).authorize(_spec(mcp_servers=["playwright"]))


def test_an_unlisted_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"is not allowed by \[orchestration\.agent_management\]\.providers"):
        _policy().authorize(_spec(model_provider="anthropic"))


def test_the_main_provider_is_always_allowed() -> None:
    settings = Settings.from_dict(
        {"llm": {"provider": "openai"}, "orchestration": {"agent_management": {"write": True}}}
    )

    ManagedAgentPolicy.from_settings(settings).authorize(_spec(model_provider="openai"))


def test_an_inherited_provider_is_allowed() -> None:
    _policy().authorize(_spec(model_provider=None))


def test_a_listed_provider_is_allowed() -> None:
    _policy(providers=["anthropic"]).authorize(_spec(model_provider="anthropic"))


def test_a_spec_with_no_grants_is_allowed() -> None:
    _policy().authorize(_spec())


def test_is_managed_definition_uses_the_directory(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    owner = tmp_path / "owner"

    assert is_managed_definition(_spec(source_path=managed / "helper_agent.md"), managed) is True
    assert is_managed_definition(_spec(source_path=owner / "helper_agent.md"), managed) is False
