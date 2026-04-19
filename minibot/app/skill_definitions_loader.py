from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from minibot.core.skills import SkillSpec
from minibot.shared.frontmatter import parse_frontmatter, split_frontmatter

logger = logging.getLogger("minibot.skill_definitions_loader")
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{2,60}$")
_DESCRIPTION_MAX_CHARS = 300


class SkillDefinitionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    enabled: bool = True


def load_skill_specs(paths: list[str] | None = None) -> list[SkillSpec]:
    resolved = resolve_skill_discovery_paths(paths)
    return _load_from_paths(resolved)


def resolve_skill_discovery_paths(paths: list[str] | None = None) -> list[tuple[Path, bool]]:
    if paths:
        return [(Path(p).expanduser().resolve(), True) for p in paths]
    return _default_discovery_paths()


def _default_discovery_paths() -> list[tuple[Path, bool]]:
    cwd = Path.cwd()
    home = Path.home()
    return [
        (cwd / ".agents" / "skills", True),
        (cwd / ".claude" / "skills", True),
        (home / ".agents" / "skills", False),
        (home / ".claude" / "skills", False),
    ]


def _load_from_paths(resolved: list[tuple[Path, bool]]) -> list[SkillSpec]:
    by_name: dict[str, tuple[SkillSpec, bool]] = {}
    for base_path, is_project_level in resolved:
        if not base_path.exists() or not base_path.is_dir():
            continue
        try:
            subdirs = sorted(p for p in base_path.iterdir() if p.is_dir())
        except OSError as exc:
            logger.warning("could not list skills directory", extra={"path": str(base_path), "error": str(exc)})
            continue
        for skill_dir in subdirs:
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            spec = _parse_skill_file(skill_file, skill_dir)
            if spec is None:
                continue
            existing = by_name.get(spec.name)
            if existing is not None:
                existing_spec, existing_project = existing
                if existing_project and not is_project_level:
                    logger.warning(
                        "skill name collision: project-level skill takes precedence",
                        extra={"skill_name": spec.name, "skipped_path": str(skill_file)},
                    )
                    continue
                if not existing_project and is_project_level:
                    logger.warning(
                        "skill name collision: project-level skill overrides user-level",
                        extra={"skill_name": spec.name, "replaced_path": str(existing_spec.skill_dir)},
                    )
                else:
                    logger.warning(
                        "skill name collision: earlier path takes precedence",
                        extra={"skill_name": spec.name, "skipped_path": str(skill_file)},
                    )
                    continue
            by_name[spec.name] = (spec, is_project_level)
    return [entry[0] for entry in by_name.values()]


def fingerprint_skill_paths(resolved: list[tuple[Path, bool]]) -> tuple[tuple[str, int, int], ...]:
    entries: list[tuple[str, int, int]] = []
    for base_path, _is_project_level in resolved:
        if not base_path.exists() or not base_path.is_dir():
            continue
        try:
            subdirs = sorted(p for p in base_path.iterdir() if p.is_dir())
        except OSError as exc:
            logger.warning("could not list skills directory", extra={"path": str(base_path), "error": str(exc)})
            continue
        for skill_dir in subdirs:
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists() or not skill_file.is_file():
                continue
            try:
                stat = skill_file.stat()
            except OSError as exc:
                logger.warning("could not stat skill file", extra={"path": str(skill_file), "error": str(exc)})
                continue
            entries.append((skill_file.as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


def _parse_skill_file(skill_file: Path, skill_dir: Path) -> SkillSpec | None:
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("could not read skill file", extra={"path": str(skill_file), "error": str(exc)})
        return None
    try:
        frontmatter_text, body = split_frontmatter(text)
    except ValueError as exc:
        logger.warning("invalid skill frontmatter", extra={"path": str(skill_file), "error": str(exc)})
        return None
    if frontmatter_text is None:
        logger.warning("skill file has no frontmatter", extra={"path": str(skill_file)})
        return None
    try:
        payload = parse_frontmatter(frontmatter_text)
    except ValueError as exc:
        logger.warning("could not parse skill frontmatter", extra={"path": str(skill_file), "error": str(exc)})
        return None
    if not isinstance(payload, dict):
        logger.warning("skill frontmatter must be a YAML object", extra={"path": str(skill_file)})
        return None
    try:
        cfg = SkillDefinitionConfig.model_validate(payload)
    except ValidationError as exc:
        logger.warning("invalid skill frontmatter fields", extra={"path": str(skill_file), "error": str(exc)})
        return None
    if not cfg.enabled:
        return None
    body = body.strip()
    if not body:
        logger.warning("skill body is empty, skipping", extra={"path": str(skill_file)})
        return None
    if not _NAME_RE.fullmatch(cfg.name):
        logger.warning(
            "skill name does not match expected pattern",
            extra={"skill_name": cfg.name, "pattern": _NAME_RE.pattern, "source": str(skill_file)},
        )
    if len(cfg.description) > _DESCRIPTION_MAX_CHARS:
        logger.warning(
            "skill description exceeds recommended length",
            extra={"skill_name": cfg.name, "length": len(cfg.description), "max": _DESCRIPTION_MAX_CHARS},
        )
    return SkillSpec(name=cfg.name, description=cfg.description, body=body, skill_dir=skill_dir)
