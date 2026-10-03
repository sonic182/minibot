from __future__ import annotations

from pathlib import Path

import pytest

from minibot.adapters.agents.definition_reader import LocalAgentDefinitionReader
from minibot.app.agent_definitions_loader import load_active_agent_specs, load_agent_specs
from minibot.config.schema import Settings


def _write_agent(directory: Path, name: str, *, tools_allow: list[str] | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tools = "" if not tools_allow else "tools_allow:\n" + "".join(f"  - {tool}\n" for tool in tools_allow)
    (directory / f"{name}.md").write_text(
        (f"---\nname: {name}\ndescription: {name}\nmode: agent\n{tools}---\n\nYou are {name}."),
        encoding="utf-8",
    )


def test_load_agent_specs_accepts_tools_allow(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "files_agent.md").write_text(
        (
            "---\n"
            "name: files_agent\n"
            "description: files\n"
            "mode: agent\n"
            "tools_allow:\n"
            "  - filesystem\n"
            "  - glob_files\n"
            "---\n\n"
            "You are files agent."
        ),
        encoding="utf-8",
    )

    specs = load_agent_specs(str(agents_dir), reader=LocalAgentDefinitionReader())

    assert len(specs) == 1
    assert specs[0].tools_allow == ["filesystem", "glob_files"]


def test_load_agent_specs_rejects_allow_and_deny_together(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "invalid_agent.md").write_text(
        (
            "---\n"
            "name: invalid_agent\n"
            "description: invalid\n"
            "mode: agent\n"
            "tools_allow:\n"
            "  - current_datetime\n"
            "tools_deny:\n"
            "  - current_datetime\n"
            "---\n\n"
            "You are invalid agent."
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_agent_specs(str(agents_dir), reader=LocalAgentDefinitionReader())


def test_load_agent_specs_rejects_unknown_frontmatter_keys(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "invalid_agent.md").write_text(
        (
            "---\n"
            "name: invalid_agent\n"
            "description: invalid\n"
            "mode: agent\n"
            "tool_allow:\n"
            "  - filesystem\n"
            "---\n\n"
            "You are invalid agent."
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid agent frontmatter"):
        load_agent_specs(str(agents_dir), reader=LocalAgentDefinitionReader())


def test_load_agent_specs_accepts_openrouter_provider_overrides(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "browser_agent.md").write_text(
        (
            "---\n"
            "name: browser_agent\n"
            "description: browser\n"
            "mode: agent\n"
            "model_provider: openrouter\n"
            "openrouter_provider_order:\n"
            "  - anthropic\n"
            "  - openai\n"
            "openrouter_provider_allow_fallbacks: true\n"
            "openrouter_provider_only:\n"
            "  - openai\n"
            "  - anthropic\n"
            "openrouter_provider_sort: price\n"
            "openrouter_provider_max_price:\n"
            "  prompt: 0.001\n"
            "  completion: 0.002\n"
            "---\n\n"
            "You are browser agent."
        ),
        encoding="utf-8",
    )

    specs = load_agent_specs(str(agents_dir), reader=LocalAgentDefinitionReader())

    assert len(specs) == 1
    assert specs[0].openrouter_provider_overrides == {
        "order": ["anthropic", "openai"],
        "allow_fallbacks": True,
        "only": ["openai", "anthropic"],
        "sort": "price",
        "max_price": {"prompt": 0.001, "completion": 0.002},
    }


def test_load_agent_specs_rejects_duplicate_names(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("first.md", "second.md"):
        (agents_dir / filename).write_text(
            ("---\nname: files_agent\ndescription: files\nmode: agent\n---\n\nYou are files agent."),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="duplicate agent name 'files_agent'"):
        load_agent_specs(str(agents_dir), reader=LocalAgentDefinitionReader())


def test_load_active_agent_specs_returns_nothing_when_specialists_are_disabled(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir, "files_agent")
    (agents_dir / "broken.md").write_text("not a definition", encoding="utf-8")

    settings = Settings.from_dict({"orchestration": {"directory": str(agents_dir), "specialists": {"enabled": False}}})

    assert load_active_agent_specs(settings, reader=LocalAgentDefinitionReader()) == []


def test_load_active_agent_specs_skips_managed_directory_while_management_is_off(tmp_path: Path) -> None:
    owner_dir = tmp_path / "agents"
    managed_dir = tmp_path / "managed"
    _write_agent(owner_dir, "files_agent")
    _write_agent(managed_dir, "browser_agent")

    settings = Settings.from_dict(
        {"orchestration": {"directory": str(owner_dir), "agent_management": {"directory": str(managed_dir)}}}
    )

    assert [spec.name for spec in load_active_agent_specs(settings, reader=LocalAgentDefinitionReader())] == [
        "files_agent"
    ]


def test_load_active_agent_specs_includes_managed_definitions(tmp_path: Path) -> None:
    owner_dir = tmp_path / "agents"
    managed_dir = tmp_path / "managed"
    _write_agent(owner_dir, "files_agent")
    _write_agent(managed_dir, "browser_agent")

    settings = Settings.from_dict(
        {
            "orchestration": {
                "directory": str(owner_dir),
                "agent_management": {"reload": True, "directory": str(managed_dir)},
            }
        }
    )

    assert [spec.name for spec in load_active_agent_specs(settings, reader=LocalAgentDefinitionReader())] == [
        "files_agent",
        "browser_agent",
    ]


def test_load_active_agent_specs_rejects_an_unauthorized_managed_definition(tmp_path: Path) -> None:
    managed_dir = tmp_path / "managed"
    _write_agent(managed_dir, "browser_agent", tools_allow=["bash"])

    settings = Settings.from_dict(
        {
            "orchestration": {
                "agent_management": {
                    "write": True,
                    "directory": str(managed_dir),
                    "tools_allow": ["filesystem"],
                }
            }
        }
    )

    with pytest.raises(ValueError, match="is not allowed by"):
        load_active_agent_specs(settings, reader=LocalAgentDefinitionReader())


def test_owner_agents_are_not_bounded_by_the_managed_ceiling(tmp_path: Path) -> None:
    owner_dir = tmp_path / "agents"
    _write_agent(owner_dir, "files_agent", tools_allow=["bash"])

    settings = Settings.from_dict({"orchestration": {"directory": str(owner_dir)}})

    assert [spec.tools_allow for spec in load_active_agent_specs(settings, reader=LocalAgentDefinitionReader())] == [
        ["bash"]
    ]


def test_load_active_agent_specs_rejects_a_managed_name_that_does_not_match_the_file(tmp_path: Path) -> None:
    managed_dir = tmp_path / "managed"
    managed_dir.mkdir(parents=True, exist_ok=True)
    (managed_dir / "browser_agent.md").write_text(
        "---\nname: files_agent\ndescription: files\nmode: agent\n---\n\nYou are files.",
        encoding="utf-8",
    )

    settings = Settings.from_dict(
        {"orchestration": {"agent_management": {"write": True, "directory": str(managed_dir)}}}
    )

    with pytest.raises(ValueError, match="must be defined in 'files_agent.md'"):
        load_active_agent_specs(settings, reader=LocalAgentDefinitionReader())


def test_load_active_agent_specs_rejects_a_managed_name_collision(tmp_path: Path) -> None:
    owner_dir = tmp_path / "agents"
    managed_dir = tmp_path / "managed"
    _write_agent(owner_dir, "files_agent")
    _write_agent(managed_dir, "files_agent")

    settings = Settings.from_dict(
        {
            "orchestration": {
                "directory": str(owner_dir),
                "agent_management": {"write": True, "directory": str(managed_dir)},
            }
        }
    )

    with pytest.raises(ValueError, match="collides with an owner-authored agent"):
        load_active_agent_specs(settings, reader=LocalAgentDefinitionReader())


def test_load_agent_specs_rejects_unknown_openrouter_provider_override(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "invalid_openrouter_agent.md").write_text(
        (
            "---\n"
            "name: invalid_openrouter_agent\n"
            "description: invalid\n"
            "mode: agent\n"
            "model_provider: openrouter\n"
            "openrouter_provider_not_a_real_key: true\n"
            "---\n\n"
            "You are invalid openrouter agent."
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid agent frontmatter"):
        load_agent_specs(str(agents_dir), reader=LocalAgentDefinitionReader())
