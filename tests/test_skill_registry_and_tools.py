from __future__ import annotations

from pathlib import Path

import pytest

from minibot.app import skill_definitions_loader
from minibot.app.skill_registry import SkillRegistry
from minibot.core.skills import SkillSource
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.skill_loader import SkillLoaderTool


def _write_skill(
    base_dir: Path,
    slug: str,
    *,
    name: str,
    description: str,
    body: str = "Use the skill.",
    extra_frontmatter: str = "",
) -> None:
    skill_dir = base_dir / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        (f"---\nname: {name}\ndescription: {description}\nenabled: true\n{extra_frontmatter}---\n\n{body}\n"),
        encoding="utf-8",
    )


def _bindings_by_name(tool: SkillLoaderTool) -> dict[str, object]:
    return {binding.tool.name: binding for binding in tool.bindings()}


@pytest.fixture
def native_skills_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand in for the skills bundled inside the package."""
    bundled = tmp_path / "bundled"
    _write_skill(bundled, "create_skill", name="create_skill", description="Author a new skill.")
    _write_skill(bundled, "import_skill", name="import_skill", description="Import a skill.")
    monkeypatch.setattr(skill_definitions_loader, "NATIVE_SKILLS_DIR", bundled)
    return bundled


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


def test_skill_registry_accepts_nested_frontmatter_and_human_readable_names(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    registry = SkillRegistry(paths=[str(skills_dir)])

    _write_skill(
        skills_dir,
        "browser-screenshot-crop-guide",
        name="Browser Screenshot Crop Guide",
        description="Capture and optionally crop web screenshots.",
        extra_frontmatter=(
            "metadata:\n  openclaw:\n    requires:\n      env:\n        - BROWSER_TOKEN\n      bins:\n        - node\n"
        ),
    )

    assert registry.refresh_if_stale() is True
    assert registry.names() == ["Browser Screenshot Crop Guide"]
    assert registry.get("Browser Screenshot Crop Guide") is not None


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


def test_native_skills_load_only_when_enabled(tmp_path: Path, native_skills_dir: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "python-review", name="python-review", description="Review Python changes.")

    without_native = SkillRegistry(paths=[str(skills_dir)])
    with_native = SkillRegistry(paths=[str(skills_dir)], native=True)

    assert without_native.names() == ["python-review"]
    assert with_native.names() == ["create_skill", "import_skill", "python-review"]
    assert with_native.get("create_skill").source is SkillSource.NATIVE
    assert with_native.get("python-review").source is SkillSource.PROJECT


def test_native_disabled_drops_only_the_named_bundled_skill(tmp_path: Path, native_skills_dir: Path) -> None:
    registry = SkillRegistry(paths=[str(tmp_path / "skills")], native=True, native_disabled=["import_skill"])

    assert registry.names() == ["create_skill"]


def test_project_skill_shadows_a_bundled_skill_of_the_same_name(tmp_path: Path, native_skills_dir: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "create_skill", name="create_skill", description="Mine.", body="Local override.")

    registry = SkillRegistry(paths=[str(skills_dir)], native=True)
    spec = registry.get("create_skill")

    assert spec.source is SkillSource.PROJECT
    assert spec.body == "Local override."


def test_native_skills_survive_configured_paths_and_write_path_is_discovered(
    tmp_path: Path, native_skills_dir: Path
) -> None:
    write_dir = tmp_path / "written"
    registry = SkillRegistry(paths=[str(tmp_path / "configured")], native=True, write_path=str(write_dir))

    assert registry.names() == ["create_skill", "import_skill"]
    assert registry.write_dir() == write_dir.resolve()

    _write_skill(write_dir, "fresh", name="fresh", description="Written at runtime.")

    assert registry.refresh_if_stale() is True
    assert "fresh" in registry.names()


@pytest.mark.asyncio
async def test_list_skills_reports_write_dir_source_and_access(tmp_path: Path, native_skills_dir: Path) -> None:
    managed_root = tmp_path / "managed"
    write_dir = managed_root / "skills"
    registry = SkillRegistry(paths=[str(tmp_path / "configured")], native=True, write_path=str(write_dir))

    inside = await _bindings_by_name(SkillLoaderTool(registry, managed_root))["list_skills"].handler({}, ToolContext())
    outside = await _bindings_by_name(SkillLoaderTool(registry, tmp_path / "elsewhere"))["list_skills"].handler(
        {}, ToolContext()
    )

    assert inside["write_dir"] == write_dir.resolve().as_posix()
    assert inside["write_dir_access"] == "filesystem"
    assert outside["write_dir_access"] == "bash"
    assert {match["source"] for match in inside["matches"]} == {"native"}
    assert write_dir.resolve().as_posix() in inside["discovery_paths"]
