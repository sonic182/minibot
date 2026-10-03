from __future__ import annotations

from pathlib import Path

import pytest

from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_roster import reload_agent_roster
from minibot.config.schema import Settings


def _definition(name: str, *, body: str = "You help.", tools: str = "") -> str:
    allow = "" if not tools else f"tools_allow:\n{tools}"
    return f"---\nname: {name}\ndescription: {name}\nmode: agent\n{allow}---\n\n{body}"


def _settings(owner_dir: Path, *, managed_dir: Path | None = None, specialists: bool = True) -> Settings:
    orchestration: dict[str, object] = {
        "directory": str(owner_dir),
        "specialists": {"enabled": specialists},
    }
    if managed_dir is not None:
        orchestration["agent_management"] = {"reload": True, "directory": str(managed_dir)}
    return Settings.from_dict({"orchestration": orchestration})


def test_reload_adds_a_new_definition_and_keeps_the_registry_object(tmp_path: Path) -> None:
    owner_dir = tmp_path / "agents"
    owner_dir.mkdir()
    settings = _settings(owner_dir)
    registry = AgentRegistry([])

    (owner_dir / "helper_agent.md").write_text(_definition("helper_agent"), encoding="utf-8")
    change = reload_agent_roster(settings=settings, registry=registry)

    assert change.added == ["helper_agent"]
    assert change.names == ["helper_agent"]
    assert registry.names() == ["helper_agent"]


def test_reload_reports_removed_and_updated(tmp_path: Path) -> None:
    owner_dir = tmp_path / "agents"
    owner_dir.mkdir()
    settings = _settings(owner_dir)
    registry = AgentRegistry([])
    (owner_dir / "helper_agent.md").write_text(_definition("helper_agent"), encoding="utf-8")
    (owner_dir / "other_agent.md").write_text(_definition("other_agent"), encoding="utf-8")
    reload_agent_roster(settings=settings, registry=registry)

    (owner_dir / "helper_agent.md").write_text(_definition("helper_agent", body="New instructions."), encoding="utf-8")
    (owner_dir / "other_agent.md").unlink()
    change = reload_agent_roster(settings=settings, registry=registry)

    assert change.updated == ["helper_agent"]
    assert change.removed == ["other_agent"]
    assert change.added == []
    assert registry.names() == ["helper_agent"]


def test_a_broken_definition_leaves_the_previous_roster(tmp_path: Path) -> None:
    owner_dir = tmp_path / "agents"
    owner_dir.mkdir()
    settings = _settings(owner_dir)
    registry = AgentRegistry([])
    (owner_dir / "helper_agent.md").write_text(_definition("helper_agent"), encoding="utf-8")
    reload_agent_roster(settings=settings, registry=registry)

    (owner_dir / "broken.md").write_text("---\nname: broken\nmode: agent\n---\n\n", encoding="utf-8")

    with pytest.raises(ValueError, match="body prompt cannot be empty"):
        reload_agent_roster(settings=settings, registry=registry)

    assert registry.names() == ["helper_agent"]


def test_an_unauthorized_managed_definition_leaves_the_previous_roster(tmp_path: Path) -> None:
    owner_dir = tmp_path / "agents"
    owner_dir.mkdir()
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir()
    settings = Settings.from_dict(
        {
            "orchestration": {
                "directory": str(owner_dir),
                "agent_management": {"write": True, "directory": str(managed_dir), "tools_allow": ["filesystem"]},
            }
        }
    )
    registry = AgentRegistry([])

    (managed_dir / "browser_agent.md").write_text(_definition("browser_agent", tools="  - bash\n"), encoding="utf-8")

    with pytest.raises(ValueError, match="is not allowed by"):
        reload_agent_roster(settings=settings, registry=registry)

    assert registry.names() == []


def test_disabling_specialists_empties_the_roster_on_reload(tmp_path: Path) -> None:
    owner_dir = tmp_path / "agents"
    owner_dir.mkdir()
    (owner_dir / "helper_agent.md").write_text(_definition("helper_agent"), encoding="utf-8")
    registry = AgentRegistry([])
    reload_agent_roster(settings=_settings(owner_dir), registry=registry)

    change = reload_agent_roster(settings=_settings(owner_dir, specialists=False), registry=registry)

    assert change.removed == ["helper_agent"]
    assert registry.names() == []


def test_reload_does_not_touch_the_main_model_budget(tmp_path: Path) -> None:
    owner_dir = tmp_path / "agents"
    owner_dir.mkdir()
    settings = _settings(owner_dir)
    settings.memory.max_history_tokens = 4242
    settings.llm.max_new_tokens = 777
    (owner_dir / "helper_agent.md").write_text(_definition("helper_agent"), encoding="utf-8")

    reload_agent_roster(settings=settings, registry=AgentRegistry([]))

    assert settings.memory.max_history_tokens == 4242
    assert settings.llm.max_new_tokens == 777
