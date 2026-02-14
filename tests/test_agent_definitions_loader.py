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
            "  - list_files\n"
            "  - create_file\n"
            "---\n\n"
            "You are files agent."
        ),
        encoding="utf-8",
    )

    specs = load_agent_specs(str(agents_dir))

    assert len(specs) == 1
    assert specs[0].tools_allow == ["list_files", "create_file"]


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
