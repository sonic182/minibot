from __future__ import annotations

from pathlib import Path

import pytest

from minibot.app.skill_registry import SkillRegistry
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.skill_loader import SkillLoaderTool


def _write_skill(base_dir: Path, slug: str, *, name: str, description: str, body: str = "Use the skill.") -> None:
    skill_dir = base_dir / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "enabled: true\n"
            "---\n\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )


def _bindings_by_name(tool: SkillLoaderTool) -> dict[str, object]:
    return {binding.tool.name: binding for binding in tool.bindings()}


def test_skill_registry_refreshes_when_new_skill_appears(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    registry = SkillRegistry(paths=[str(skills_dir)])

    assert registry.is_empty() is True

    _write_skill(skills_dir, "python-review", name="python-review", description="Review Python changes.")

    assert registry.refresh_if_stale() is True
    assert registry.names() == ["python-review"]


def test_skill_registry_refreshes_when_skill_is_deleted(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "python-review", name="python-review", description="Review Python changes.")
    registry = SkillRegistry(paths=[str(skills_dir)])

    assert registry.names() == ["python-review"]

    (skills_dir / "python-review" / "SKILL.md").unlink()

    assert registry.refresh_if_stale() is True
    assert registry.names() == []


@pytest.mark.asyncio
async def test_list_skills_returns_all_and_supports_live_refresh(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    registry = SkillRegistry(paths=[str(skills_dir)])
    tool = SkillLoaderTool(registry)
    binding = _bindings_by_name(tool)["list_skills"]

    empty_result = await binding.handler({}, ToolContext())

    assert empty_result["ok"] is True
    assert empty_result["matches"] == []

    _write_skill(skills_dir, "python-review", name="python-review", description="Review Python changes.")

    refreshed = await binding.handler({}, ToolContext())

    assert [match["name"] for match in refreshed["matches"]] == ["python-review"]


@pytest.mark.asyncio
async def test_list_skills_prefers_exact_prefix_and_uses_fuzzy_fallback(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "python-review", name="python-review", description="Review Python changes.")
    _write_skill(skills_dir, "python-refactor", name="python-refactor", description="Refactor Python modules.")
    _write_skill(skills_dir, "deploy-helper", name="deploy-helper", description="Deploy apps safely.")
    registry = SkillRegistry(paths=[str(skills_dir)])
    tool = SkillLoaderTool(registry)
    binding = _bindings_by_name(tool)["list_skills"]

    exactish = await binding.handler({"query": "python"}, ToolContext())
    fuzzy = await binding.handler({"query": "pythn revu"}, ToolContext())

    assert [match["name"] for match in exactish["matches"][:2]] == ["python-review", "python-refactor"]
    assert exactish["used_fuzzy_fallback"] is False
    assert fuzzy["matches"][0]["name"] == "python-review"
    assert fuzzy["used_fuzzy_fallback"] is True


@pytest.mark.asyncio
async def test_activate_skill_uses_live_registry_state(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    registry = SkillRegistry(paths=[str(skills_dir)])
    tool = SkillLoaderTool(registry)
    binding = _bindings_by_name(tool)["activate_skill"]

    _write_skill(skills_dir, "python-review", name="python-review", description="Review Python changes.")

    found = await binding.handler({"name": "python-review"}, ToolContext())

    assert found["ok"] is True
    assert found["skill"] == "python-review"
    assert "Use the skill." in found["instructions"]

    (skills_dir / "python-review" / "SKILL.md").unlink()

    missing = await binding.handler({"name": "python-review"}, ToolContext())

    assert missing["ok"] is False
    assert missing["error_code"] == "skill_not_found"
