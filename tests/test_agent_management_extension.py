from __future__ import annotations

import logging
from pathlib import Path

import pytest

from minibot.adapters.agents.definition_reader import LocalAgentDefinitionReader
from minibot.adapters.agents.managed_store import LocalManagedAgentStore
from minibot.app.agent_management import AgentManagementService
from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_roster import AgentRosterChange, reload_agent_roster
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionContext
from minibot.config.schema import Settings
from minibot.core.tools import ToolContext
from minibot.extensions.tools import agent_management as extension


def _definition(*, name: str = "helper_agent", tools: str = "  - write_file\n") -> str:
    return f"---\nname: {name}\ndescription: helper\nmode: agent\ntools_allow:\n{tools}---\n\nYou are a helper."


def _context(tmp_path: Path, *, reload: bool, write: bool) -> ExtensionContext:
    settings = Settings.from_dict(
        {
            "orchestration": {
                "directory": str(tmp_path / "agents"),
                "agent_management": {
                    "reload": reload,
                    "write": write,
                    "directory": str(tmp_path / "managed"),
                    "tools_allow": ["write_file"],
                },
            }
        }
    )
    registry = AgentRegistry([])

    async def refresh() -> AgentRosterChange:
        return await reload_agent_roster(settings=settings, registry=registry, reader=LocalAgentDefinitionReader())

    service = AgentManagementService(
        settings=settings,
        store=LocalManagedAgentStore(tmp_path / "managed"),
        reader=LocalAgentDefinitionReader(),
        refresh=refresh,
    )
    return ExtensionContext(
        name="minibot.extensions.tools.agent_management",
        config={},
        settings=settings,
        event_bus=EventBus(),
        logger=logging.getLogger("test.agent_management"),
        agent_management=service,
    )


def _bindings(context: ExtensionContext) -> dict[str, object]:
    extension.register(context)
    return {binding.tool.name: binding for binding in context.tools}


def test_no_tools_when_management_is_off(tmp_path: Path) -> None:
    assert _bindings(_context(tmp_path, reload=False, write=False)) == {}


def test_reload_only_registers_reload_agents(tmp_path: Path) -> None:
    assert set(_bindings(_context(tmp_path, reload=True, write=False))) == {"reload_agents"}


def test_writes_register_the_management_tools(tmp_path: Path) -> None:
    names = set(_bindings(_context(tmp_path, reload=False, write=True)))

    assert names == {"create_agent", "update_agent", "delete_agent"}


def test_both_switches_register_everything(tmp_path: Path) -> None:
    names = set(_bindings(_context(tmp_path, reload=True, write=True)))

    assert names == {"reload_agents", "create_agent", "update_agent", "delete_agent"}


def test_workers_register_nothing(tmp_path: Path) -> None:
    context = _context(tmp_path, reload=True, write=True)
    context.entrypoint = "worker"

    assert _bindings(context) == {}


@pytest.mark.asyncio
async def test_create_then_reload_then_delete(tmp_path: Path) -> None:
    bindings = _bindings(_context(tmp_path, reload=True, write=True))
    context = ToolContext(channel="console")

    created = await bindings["create_agent"].handler(  # type: ignore[attr-defined]
        {"name": "helper_agent", "definition": _definition()}, context
    )
    assert created["ok"] is True
    assert created["added"] == ["helper_agent"]

    reloaded = await bindings["reload_agents"].handler({}, context)  # type: ignore[attr-defined]
    assert reloaded["ok"] is True
    assert reloaded["names"] == ["helper_agent"]

    deleted = await bindings["delete_agent"].handler({"name": "helper_agent"}, context)  # type: ignore[attr-defined]
    assert deleted["ok"] is True
    assert deleted["removed"] == ["helper_agent"]


@pytest.mark.asyncio
async def test_create_reports_an_unauthorized_tool(tmp_path: Path) -> None:
    bindings = _bindings(_context(tmp_path, reload=False, write=True))

    outcome = await bindings["create_agent"].handler(  # type: ignore[attr-defined]
        {"name": "helper_agent", "definition": _definition(tools="  - bash\n")},
        ToolContext(channel="console"),
    )

    assert outcome["ok"] is False
    assert "not allowed by" in str(outcome["error"])


@pytest.mark.asyncio
async def test_update_requires_an_existing_agent(tmp_path: Path) -> None:
    bindings = _bindings(_context(tmp_path, reload=False, write=True))

    outcome = await bindings["update_agent"].handler(  # type: ignore[attr-defined]
        {"name": "helper_agent", "definition": _definition()},
        ToolContext(channel="console"),
    )

    assert outcome["ok"] is False
    assert "does not exist" in str(outcome["error"])
