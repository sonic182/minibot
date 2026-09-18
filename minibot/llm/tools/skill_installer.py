from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiosonic
from aiosonic.timeout import Timeouts
from llm_async.models import Tool

from minibot.app.skill_definitions_loader import NATIVE_SKILLS_DIR, parse_skill_file
from minibot.app.skill_registry import SkillRegistry
from minibot.core.skills import SkillSource, SkillSpec
from minibot.llm.tools.arg_utils import optional_bool, optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import strict_object

MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_EXTRACT_BYTES = 25 * 1024 * 1024
MAX_EXTRACT_FILES = 1000
_MAX_DEPTH = 5
_SKIP_DIRS = frozenset({".git", "node_modules", "dist", "build", "__pycache__"})
_LOCK_FILE = "skills-lock.json"
_UNSAFE_DIR_CHARS = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class ResolvedSource:
    source: str
    url: str
    source_type: str
    ref: str | None = None
    subpath: str = ""
    skill: str | None = None


class SkillInstallerTool:
    """Install skills from GitHub or an archive URL into the skill write directory.

    Enabled by ``[tools.skills] install = true``. Exposes ``install_skill``, a Python
    counterpart of ``npx skills add``: ``owner/repo``, ``owner/repo@skill``,
    ``owner/repo/path``, GitHub ``tree``/``blob`` URLs, and ``.zip``/``.tar.gz``/``SKILL.md``
    URLs over HTTPS. Every candidate is validated with the same parser the runtime uses; the
    installed skill is recorded in ``skills-lock.json`` next to it, in the npm ``skills``
    tool's format. Nothing is activated after installing.
    """

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        return Tool(
            name="install_skill",
            description=load_tool_description("install_skill"),
            parameters=strict_object(
                properties={
                    "source": {
                        "type": "string",
                        "description": "owner/repo, owner/repo@skill, owner/repo/path, a GitHub tree/blob URL, "
                        "or an https URL to a .zip, .tar.gz or SKILL.md.",
                    },
                    "skill": {
                        "type": ["string", "null"],
                        "description": "Skill name to install when the source holds more than one.",
                    },
                    "install": {
                        "type": ["boolean", "null"],
                        "description": "false (default) previews only; true installs.",
                    },
                    "force": {
                        "type": ["boolean", "null"],
                        "description": "Overwrite an installed skill of the same name.",
                    },
                    "dest": {
                        "type": ["string", "null"],
                        "description": "One of the skill discovery paths; defaults to the write directory.",
                    },
                },
                required=["source"],
            ),
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        resolved = parse_source(require_non_empty_str(payload, "source"))
        skill = optional_str(payload.get("skill"), error_message="skill must be a string") or resolved.skill
        install = optional_bool(payload.get("install"), default=False, error_message="install must be a boolean")
        force = optional_bool(payload.get("force"), default=False, error_message="force must be a boolean")
        dest = self._resolve_dest(optional_str(payload.get("dest"), error_message="dest must be a string"))
        archive = await download(resolved.url)
        result = await asyncio.to_thread(_process, resolved, archive, skill, install, force, dest)
        if install and result.get("ok"):
            self._registry.refresh_if_stale()
        return result

    def _resolve_dest(self, dest: str | None) -> Path:
        if dest is None:
            return self._registry.write_dir()
        path = Path(dest).expanduser().resolve()
        allowed = [p for p in self._registry.discovery_paths() if p != NATIVE_SKILLS_DIR]
        if path not in allowed:
            raise ValueError(f"dest must be one of: {', '.join(p.as_posix() for p in allowed)}")
        return path


def parse_source(raw: str) -> ResolvedSource:
    text = raw.strip()
    if "://" not in text:
        base, _, skill = text.partition("@")
        parts = [part for part in base.split("/") if part]
        if len(parts) < 2:
            raise ValueError("source must be owner/repo[/path][@skill] or an https URL")
        return _github(parts[0], parts[1], None, "/".join(parts[2:]), skill or None)
    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise ValueError("only https sources are supported")
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.hostname == "github.com" and len(parts) >= 2:
        ref, subpath = None, ""
        # ponytail: a ref containing "/" is read as its first segment; resolve refs via the API if needed.
        if len(parts) >= 4 and parts[2] in ("tree", "blob"):
            ref, subpath = parts[3], "/".join(parts[4:]).removesuffix("SKILL.md").rstrip("/")
        return _github(parts[0], parts[1].removesuffix(".git"), ref, subpath, None)
    return ResolvedSource(source=text, url=text, source_type="url")


def _github(owner: str, repo: str, ref: str | None, subpath: str, skill: str | None) -> ResolvedSource:
    return ResolvedSource(
        source=f"{owner}/{repo}",
        url=f"https://codeload.github.com/{owner}/{repo}/tar.gz/{ref or 'HEAD'}",
        source_type="github",
        ref=ref,
        subpath=subpath,
        skill=skill,
    )


async def download(url: str) -> bytes:
    timeouts = Timeouts(sock_connect=10, sock_read=30)
    async with aiosonic.HTTPClient() as client:
        response = await client.request(url, follow=True, max_redirects=5, timeouts=timeouts)
        if response.status_code != 200:
            raise ValueError(f"download failed: HTTP {response.status_code} for {url}")
        # ponytail: reads the whole body before the size check; stream with read_chunks if memory matters.
        body = await response.content()
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"download exceeds {MAX_DOWNLOAD_BYTES} bytes")
    return body


def _process(
    resolved: ResolvedSource,
    archive: bytes,
    skill: str | None,
    install: bool,
    force: bool,
    dest: Path,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="minibot-skill-") as tmp:
        root = _extract(archive, Path(tmp))
        search_root = (root / resolved.subpath).resolve()
        if not search_root.is_relative_to(root) or not search_root.is_dir():
            return _error("path_not_found", f"'{resolved.subpath}' does not exist in {resolved.source}")
        specs, invalid = _discover(search_root)
        if skill is not None:
            wanted = skill.casefold()
            specs = [spec for spec in specs if wanted in (spec.name.casefold(), spec.skill_dir.name.casefold())]
        base = {"source": resolved.source, "resolved_url": resolved.url, "ref": resolved.ref, "dest": dest.as_posix()}
        if not install:
            return {
                "ok": True,
                **base,
                "skills": [_describe(spec, root) for spec in specs],
                "invalid": [{"skill_path": path.relative_to(root).as_posix(), "error": err} for path, err in invalid],
            }
        if not specs:
            named = f" named {skill!r}" if skill else ""
            return _error("skill_not_found", f"no valid skill{named} in {resolved.source}")
        if len(specs) > 1:
            names = ", ".join(spec.name for spec in specs)
            return _error("skill_selection_required", f"{resolved.source} holds several skills; pick one: {names}")
        return _install(specs[0], root, resolved, dest, force, base)


def _extract(archive: bytes, target: Path) -> Path:
    buffer = io.BytesIO(archive)
    if zipfile.is_zipfile(buffer):
        with zipfile.ZipFile(buffer) as bundle:
            members = [info for info in bundle.infolist() if not info.is_dir()]
            _check_limits(len(members), sum(info.file_size for info in members))
            bundle.extractall(target)
    else:
        buffer.seek(0)
        try:
            bundle = tarfile.open(fileobj=buffer)
        except tarfile.ReadError:
            (target / "SKILL.md").write_bytes(archive)
            return target
        with bundle:
            members = [member for member in bundle.getmembers() if member.isfile()]
            _check_limits(len(members), sum(member.size for member in members))
            try:
                bundle.extractall(target, filter="data")
            except tarfile.TarError as exc:
                raise ValueError(f"unsafe archive: {exc}") from exc
    entries = list(target.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0].resolve()
    return target.resolve()


def _check_limits(files: int, size: int) -> None:
    if files > MAX_EXTRACT_FILES:
        raise ValueError(f"archive holds more than {MAX_EXTRACT_FILES} files")
    if size > MAX_EXTRACT_BYTES:
        raise ValueError(f"archive expands beyond {MAX_EXTRACT_BYTES} bytes")


def _discover(root: Path) -> tuple[list[SkillSpec], list[tuple[Path, str]]]:
    found: list[Path] = []

    def walk(directory: Path, depth: int) -> None:
        if (directory / "SKILL.md").is_file():
            found.append(directory)
            return
        if depth >= _MAX_DEPTH:
            return
        for child in sorted(directory.iterdir()):
            if child.is_dir() and not child.is_symlink() and child.name not in _SKIP_DIRS:
                walk(child, depth + 1)

    walk(root, 0)
    specs: dict[str, SkillSpec] = {}
    invalid: list[tuple[Path, str]] = []
    for skill_dir in sorted(found, key=lambda path: len(path.parts)):
        try:
            spec = parse_skill_file(skill_dir / "SKILL.md", skill_dir, SkillSource.PROJECT)
        except ValueError as exc:
            invalid.append((skill_dir, str(exc)))
            continue
        specs.setdefault(spec.name, spec)
    return list(specs.values()), invalid


def _describe(spec: SkillSpec, root: Path) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "compatibility": spec.compatibility or None,
        "skill_path": spec.skill_dir.relative_to(root).as_posix(),
        "files": sorted(path.relative_to(spec.skill_dir).as_posix() for path in _files(spec.skill_dir)),
    }


def _install(
    spec: SkillSpec,
    root: Path,
    resolved: ResolvedSource,
    dest: Path,
    force: bool,
    base: dict[str, Any],
) -> dict[str, Any]:
    dir_name = _UNSAFE_DIR_CHARS.sub("-", spec.name.lower()).strip("-.")
    if not dir_name:
        return _error("invalid_skill_name", f"cannot derive a directory name from {spec.name!r}")
    target = dest / dir_name
    if target.exists() and not force:
        return _error("skill_exists", f"{target.as_posix()} already exists; pass force to overwrite")
    dest.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(spec.skill_dir, target)
    entry: dict[str, Any] = {"source": resolved.source, "sourceType": resolved.source_type}
    if resolved.ref:
        entry["ref"] = resolved.ref
    if resolved.source_type == "github":
        entry["skillPath"] = (spec.skill_dir.relative_to(root) / "SKILL.md").as_posix()
    entry["computedHash"] = folder_hash(target)
    lock_file = _write_lock(dest, spec.name, entry)
    return {
        "ok": True,
        **base,
        "installed": spec.name,
        "path": target.as_posix(),
        "compatibility": spec.compatibility or None,
        "lock_file": lock_file.as_posix(),
    }


def _files(directory: Path) -> list[Path]:
    return [
        path
        for path in directory.rglob("*")
        if path.is_file() and not {".git", "node_modules"} & set(path.relative_to(directory).parts)
    ]


def folder_hash(directory: Path) -> str:
    """SHA-256 over sorted relative paths and contents, as npm ``skills`` computes ``computedHash``."""
    digest = hashlib.sha256()
    for path in sorted(_files(directory), key=lambda item: item.relative_to(directory).as_posix()):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_lock(dest: Path, name: str, entry: dict[str, Any]) -> Path:
    lock_file = dest / _LOCK_FILE
    try:
        lock = json.loads(lock_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lock = {}
    skills = lock.get("skills") if isinstance(lock, dict) else None
    if not isinstance(skills, dict):
        skills = {}
    skills[name] = entry
    lock = {"version": 1, "skills": dict(sorted(skills.items()))}
    lock_file.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return lock_file


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "error": message}
