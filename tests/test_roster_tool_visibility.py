"""The tool-list rebuild a roster reload performs, without standing up a Dispatcher."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from minibot.adapters.config.schema import Settings
from minibot.app.agent_registry import AgentRegistry
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.tool_capabilities import main_agent_tool_view
from minibot.app.tool_factory import build_enabled_tools
from minibot.core.agents import AgentSpec


class _MemoryStub:
    async def append_history(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content

    async def get_history(self, session_id: str, limit: int | None = None) -> list[object]:
        del session_id, limit
        return []

    async def count_history(self, session_id: str) -> int:
        del session_id
        return 0


def _rebuild(settings: Settings, registry: AgentRegistry) -> tuple[list[str], list[str]]:
    """Exactly what Dispatcher.refresh_agent_roster does: rebuild, then re-filter."""
    tools = build_enabled_tools(
        settings,
        _MemoryStub(),
        agent_registry=registry,
        llm_factory=LLMClientFactory(settings),
    )
    view = main_agent_tool_view(
        tools=tools,
        orchestration_config=settings.orchestration,
        agent_specs=registry.all(),
    )
    return sorted(binding.tool.name for binding in view.tools), view.hidden_tool_names


def _spec(name: str, **overrides: Any) -> AgentSpec:
    payload: dict[str, Any] = {
        "name": name,
        "description": name,
        "system_prompt": "work",
        "source_path": Path(f"agents/{name}.md"),
    }
    payload.update(overrides)
    return AgentSpec(**payload)


def _settings(**orchestration: Any) -> Settings:
    return Settings.from_dict({"tools": {"calculator": {"enabled": True}}, "orchestration": orchestration})


def test_a_zero_agent_start_gains_fetch_agent_info_on_reload() -> None:
    settings = _settings()
    registry = AgentRegistry([])

    before, _ = _rebuild(settings, registry)
    registry.replace_all([_spec("researcher")])
    after, _ = _rebuild(settings, registry)

    assert "fetch_agent_info" not in before
    assert "fetch_agent_info" in after


def test_exclusive_ownership_hides_a_newly_added_specialist_tool() -> None:
    settings = _settings(tool_ownership_mode="exclusive")
    registry = AgentRegistry([])

    before, before_hidden = _rebuild(settings, registry)
    registry.replace_all([_spec("files_agent", tools_allow=["calculate_expression"])])
    after, after_hidden = _rebuild(settings, registry)

    assert "calculate_expression" in before
    assert "calculate_expression" not in after
    assert before_hidden == []
    assert after_hidden == ["calculate_expression"]


def test_removing_a_specialist_gives_its_tool_back_to_the_main_agent() -> None:
    settings = _settings(tool_ownership_mode="exclusive")
    registry = AgentRegistry([_spec("files_agent", tools_allow=["calculate_expression"])])

    _, hidden = _rebuild(settings, registry)
    registry.replace_all([])
    visible, after_hidden = _rebuild(settings, registry)

    assert hidden == ["calculate_expression"]
    assert "calculate_expression" in visible
    assert after_hidden == []


def test_disabled_specialists_never_reserve_a_tool() -> None:
    settings = _settings(tool_ownership_mode="exclusive", specialists={"enabled": False})
    registry = AgentRegistry([])

    visible, hidden = _rebuild(settings, registry)

    assert "calculate_expression" in visible
    assert hidden == []


def test_an_owner_specialist_keeps_mcp_access_the_main_agent_is_denied() -> None:
    # The default config denies `mcp*` to the main agent while a specialist may still claim a server.
    settings = Settings.from_dict(
        {
            "tools": {"mcp": {"enabled": True, "servers": [{"name": "playwright", "command": "playwright-mcp"}]}},
            "orchestration": {
                "tool_ownership_mode": "exclusive_mcp",
                "main_agent": {"tools_deny": ["mcp*"]},
            },
        }
    )
    registry = AgentRegistry([_spec("browser_agent", mcp_servers=["playwright"])])

    visible, _ = _rebuild(settings, registry)

    assert not any(name.startswith("mcp_") for name in visible)
