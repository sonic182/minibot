from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from minibot.adapters.config.schema import SkillsToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.app import skill_definitions_loader
from minibot.app.skill_registry import SkillRegistry
from minibot.core.skills import SkillSource
from minibot.llm.tools import skill_installer
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.skill_installer import SkillInstallerTool, folder_hash
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
        (f"---\nname: {name}\ndescription: {description}\n{extra_frontmatter}---\n\n{body}\n"),
        encoding="utf-8",
    )


def _bindings_by_name(tool: SkillLoaderTool) -> dict[str, object]:
    return {binding.tool.name: binding for binding in tool.bindings()}


@pytest.fixture
def native_skills_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand in for the skills bundled inside the package."""
    bundled = tmp_path / "bundled"
    _write_skill(bundled, "create-skill", name="create-skill", description="Author a new skill.")
    _write_skill(bundled, "install-skill", name="install-skill", description="Install a skill.")
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


@pytest.mark.asyncio
async def test_activate_skill_reports_compatibility_and_ignores_the_enabled_flag(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir,
        "deploy",
        name="deploy",
        description="Deploy the app.",
        extra_frontmatter="enabled: false\ncompatibility: Requires bash and git\n",
    )
    tool = SkillLoaderTool(SkillRegistry(paths=[str(skills_dir)]))

    result = await _bindings_by_name(tool)["activate_skill"].handler({"name": "deploy"}, ToolContext())

    assert result["ok"] is True
    assert result["compatibility"] == "Requires bash and git"


def test_native_skills_load_only_when_enabled(tmp_path: Path, native_skills_dir: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "python-review", name="python-review", description="Review Python changes.")

    without_native = SkillRegistry(paths=[str(skills_dir)])
    with_native = SkillRegistry(paths=[str(skills_dir)], native=True)

    assert without_native.names() == ["python-review"]
    assert with_native.names() == ["create-skill", "install-skill", "python-review"]
    assert with_native.get("create-skill").source is SkillSource.NATIVE
    assert with_native.get("python-review").source is SkillSource.PROJECT


def test_native_disabled_drops_only_the_named_bundled_skill(tmp_path: Path, native_skills_dir: Path) -> None:
    registry = SkillRegistry(paths=[str(tmp_path / "skills")], native=True, native_disabled=["install-skill"])

    assert registry.names() == ["create-skill"]


def test_project_skill_shadows_a_bundled_skill_of_the_same_name(tmp_path: Path, native_skills_dir: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "create-skill", name="create-skill", description="Mine.", body="Local override.")

    registry = SkillRegistry(paths=[str(skills_dir)], native=True)
    spec = registry.get("create-skill")

    assert spec.source is SkillSource.PROJECT
    assert spec.body == "Local override."


def test_native_skills_survive_configured_paths_and_write_path_is_discovered(
    tmp_path: Path, native_skills_dir: Path
) -> None:
    write_dir = tmp_path / "written"
    registry = SkillRegistry(paths=[str(tmp_path / "configured")], native=True, write_path=str(write_dir))

    assert registry.names() == ["create-skill", "install-skill"]
    assert registry.write_dir() == write_dir.resolve()

    _write_skill(write_dir, "fresh", name="fresh", description="Written at runtime.")

    assert registry.refresh_if_stale() is True
    assert "fresh" in registry.names()


async def _list_skills(registry: SkillRegistry, storage: LocalFileStorage | None, bash_enabled: bool) -> dict:
    tool = SkillLoaderTool(registry, storage, bash_enabled)
    return await _bindings_by_name(tool)["list_skills"].handler({}, ToolContext())


def _storage(root: Path, *, allow_outside_root: bool = False) -> LocalFileStorage:
    return LocalFileStorage(str(root), max_write_bytes=64000, allow_outside_root=allow_outside_root)


@pytest.mark.asyncio
async def test_list_skills_reports_write_dir_and_source(tmp_path: Path, native_skills_dir: Path) -> None:
    managed_root = tmp_path / "managed"
    write_dir = managed_root / "skills"
    registry = SkillRegistry(paths=[str(tmp_path / "configured")], native=True, write_path=str(write_dir))

    result = await _list_skills(registry, _storage(managed_root), False)

    assert result["write_dir"] == write_dir.resolve().as_posix()
    assert {match["source"] for match in result["matches"]} == {"native"}
    assert write_dir.resolve().as_posix() in result["discovery_paths"]


@pytest.mark.asyncio
async def test_write_dir_access_reflects_the_writers_actually_available(
    tmp_path: Path, native_skills_dir: Path
) -> None:
    managed_root = tmp_path / "managed"
    inside_root = SkillRegistry(native=True, write_path=str(managed_root / "skills"))
    outside_root = SkillRegistry(native=True, write_path=str(tmp_path / "elsewhere"))

    confined = await _list_skills(inside_root, _storage(managed_root), False)
    escaped_confined = await _list_skills(outside_root, _storage(managed_root), False)
    escaped_with_bash = await _list_skills(outside_root, _storage(managed_root), True)
    yolo = await _list_skills(outside_root, _storage(managed_root, allow_outside_root=True), False)
    no_storage = await _list_skills(outside_root, None, False)

    assert confined["write_dir_access"] == "filesystem"
    assert confined["write_dir_filesystem_path"] == "skills"
    # Out of the filesystem tool's reach and bash is off: say so rather than name a missing tool.
    assert escaped_confined["write_dir_access"] == "unavailable"
    assert escaped_confined["write_dir_filesystem_path"] is None
    assert escaped_with_bash["write_dir_access"] == "bash"
    # allow_outside_root lets the filesystem tool take an absolute path anywhere.
    assert yolo["write_dir_access"] == "filesystem"
    assert yolo["write_dir_filesystem_path"] == (tmp_path / "elsewhere").resolve().as_posix()
    assert no_storage["write_dir_access"] == "unavailable"


def test_bundled_install_skill_is_hidden_until_install_is_enabled(tmp_path: Path, native_skills_dir: Path) -> None:
    def names(install: bool) -> list[str]:
        config = SkillsToolConfig(install=install)
        return SkillRegistry(
            paths=[str(tmp_path / "skills")], native=True, native_disabled=config.disabled_native_skills
        ).names()

    assert names(install=False) == ["create-skill"]
    assert names(install=True) == ["create-skill", "install-skill"]


def _tarball(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _serve(monkeypatch: pytest.MonkeyPatch, archive: bytes) -> list[str]:
    requested: list[str] = []

    async def fake_download(url: str) -> bytes:
        requested.append(url)
        return archive

    monkeypatch.setattr(skill_installer, "download", fake_download)
    return requested


@pytest.mark.asyncio
async def test_install_skill_previews_installs_and_is_discovered_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested = _serve(
        monkeypatch,
        _tarball(
            {
                "skills-HEAD/README.md": "repo",
                "skills-HEAD/skills/find-skills/SKILL.md": (
                    "---\nname: find-skills\ndescription: Find skills.\ncompatibility: Requires npx\n---\n\nRun it.\n"
                ),
                "skills-HEAD/skills/find-skills/references/usage.md": "usage",
                "skills-HEAD/skills/broken/SKILL.md": "---\nname: broken\ndescription: Empty.\n---\n",
            }
        ),
    )
    write_dir = tmp_path / "skills"
    registry = SkillRegistry(paths=[str(tmp_path / "configured")], write_path=str(write_dir))
    handler = SkillInstallerTool(registry).bindings()[0].handler

    preview = await handler({"source": "vercel-labs/skills"}, ToolContext())

    assert requested == ["https://codeload.github.com/vercel-labs/skills/tar.gz/HEAD"]
    assert preview["skills"] == [
        {
            "name": "find-skills",
            "description": "Find skills.",
            "compatibility": "Requires npx",
            "skill_path": "skills/find-skills",
            "files": ["SKILL.md", "references/usage.md"],
        }
    ]
    assert preview["invalid"] == [{"skill_path": "skills/broken", "error": "skill body is empty"}]
    assert not write_dir.exists()

    install = {"source": "vercel-labs/skills@find-skills", "install": True}
    installed = await handler(install, ToolContext())

    assert installed["ok"] is True
    assert (write_dir / "find-skills" / "references" / "usage.md").is_file()
    assert registry.get("find-skills").compatibility == "Requires npx"
    assert json.loads((write_dir / "skills-lock.json").read_text(encoding="utf-8")) == {
        "version": 1,
        "skills": {
            "find-skills": {
                "source": "vercel-labs/skills",
                "sourceType": "github",
                "skillPath": "skills/find-skills/SKILL.md",
                "computedHash": folder_hash(write_dir / "find-skills"),
            }
        },
    }

    assert (await handler(install, ToolContext()))["error_code"] == "skill_exists"
    assert (await handler({**install, "force": True}, ToolContext()))["ok"] is True


@pytest.mark.asyncio
async def test_install_skill_rejects_archive_members_escaping_the_extract_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, _tarball({"../evil/SKILL.md": "---\nname: evil\n---\n\nPwned.\n"}))
    handler = SkillInstallerTool(SkillRegistry(write_path=str(tmp_path / "skills"))).bindings()[0].handler

    with pytest.raises(ValueError, match="unsafe archive"):
        await handler({"source": "https://example.com/skill.tar.gz", "install": True}, ToolContext())

    assert not (tmp_path / "skills").exists()
