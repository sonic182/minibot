from __future__ import annotations

from pathlib import Path

import pytest

from minibot.app.agent_definitions_loader import load_agent_specs


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

    specs = load_agent_specs(str(agents_dir))

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
        load_agent_specs(str(agents_dir))


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
        load_agent_specs(str(agents_dir))


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

    specs = load_agent_specs(str(agents_dir))

    assert len(specs) == 1
    assert specs[0].openrouter_provider_overrides == {
        "order": ["anthropic", "openai"],
        "allow_fallbacks": True,
        "only": ["openai", "anthropic"],
        "sort": "price",
        "max_price": {"prompt": 0.001, "completion": 0.002},
    }


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
        load_agent_specs(str(agents_dir))
