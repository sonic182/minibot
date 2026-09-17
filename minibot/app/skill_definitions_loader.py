from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from minibot.core.skills import SKILL_SOURCE_RANK, SkillSource, SkillSpec
from minibot.shared.frontmatter import parse_scalar, split_frontmatter

logger = logging.getLogger("minibot.skill_definitions_loader")
_NAME_RE = re.compile(r"^[^\r\n/\\]{2,60}$")
_DESCRIPTION_MAX_CHARS = 300
_SKILL_DIR_NAMES = (".minibot", ".agents")
NATIVE_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


class SkillDefinitionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    enabled: bool = True


def load_skill_specs(
    paths: list[str] | None = None,
    *,
    native: bool = False,
    native_disabled: Iterable[str] = (),
    write_path: str | None = None,
) -> list[SkillSpec]:
    resolved = resolve_skill_discovery_paths(paths, native=native, write_path=write_path)
    return _load_from_paths(resolved, native_disabled=frozenset(native_disabled))


def resolve_skill_discovery_paths(
    paths: list[str] | None = None,
    *,
    native: bool = False,
    write_path: str | None = None,
) -> list[tuple[Path, SkillSource]]:
    resolved: list[tuple[Path, SkillSource]] = []
    if write_path:
        resolved.append((Path(write_path).expanduser().resolve(), SkillSource.PROJECT))
    if paths:
        resolved.extend((Path(path).expanduser().resolve(), SkillSource.PROJECT) for path in paths)
    else:
        resolved.extend(_default_discovery_paths())
    # Appended last and unconditionally: `paths` replaces the default list, so a native tier
    # living inside it would vanish the moment anyone configures `paths`.
    if native:
        resolved.append((NATIVE_SKILLS_DIR, SkillSource.NATIVE))
    return _deduplicate_paths(resolved)


def resolve_skill_write_dir(paths: list[str] | None = None, write_path: str | None = None) -> Path:
    if write_path:
        return Path(write_path).expanduser().resolve()
    if paths:
        return Path(paths[0]).expanduser().resolve()
    return _default_discovery_paths()[0][0]


def _deduplicate_paths(resolved: list[tuple[Path, SkillSource]]) -> list[tuple[Path, SkillSource]]:
    seen: set[Path] = set()
    unique: list[tuple[Path, SkillSource]] = []
    for base_path, source in resolved:
        if base_path in seen:
            continue
        seen.add(base_path)
        unique.append((base_path, source))
    return unique


def _default_discovery_paths() -> list[tuple[Path, SkillSource]]:
    cwd = Path.cwd()
    home = Path.home()
    return [
        *((cwd / name / "skills", SkillSource.PROJECT) for name in _SKILL_DIR_NAMES),
        *((home / name / "skills", SkillSource.USER) for name in _SKILL_DIR_NAMES),
    ]


def _load_from_paths(
    resolved: list[tuple[Path, SkillSource]],
    *,
    native_disabled: frozenset[str] = frozenset(),
) -> list[SkillSpec]:
    by_name: dict[str, SkillSpec] = {}
    for base_path, source in resolved:
        if not base_path.exists() or not base_path.is_dir():
            continue
        try:
            subdirs = sorted(path for path in base_path.iterdir() if path.is_dir())
        except OSError as exc:
            logger.warning("could not list skills directory", extra={"path": str(base_path), "error": str(exc)})
            continue
        for skill_dir in subdirs:
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            spec = _parse_skill_file(skill_file, skill_dir, source)
            if spec is None:
                continue
            if source is SkillSource.NATIVE and spec.name in native_disabled:
                continue
            existing = by_name.get(spec.name)
            if existing is not None and not _takes_precedence(spec, existing):
                logger.warning(
                    "skill name collision: earlier source takes precedence",
                    extra={
                        "skill_name": spec.name,
                        "skipped_path": str(skill_file),
                        "skipped_source": str(spec.source),
                        "kept_source": str(existing.source),
                    },
                )
                continue
            if existing is not None:
                logger.warning(
                    "skill name collision: overriding a lower-precedence skill",
                    extra={
                        "skill_name": spec.name,
                        "replaced_path": str(existing.skill_dir),
                        "replaced_source": str(existing.source),
                        "kept_source": str(spec.source),
                    },
                )
            by_name[spec.name] = spec
    return list(by_name.values())


def _takes_precedence(candidate: SkillSpec, existing: SkillSpec) -> bool:
    return SKILL_SOURCE_RANK[candidate.source] < SKILL_SOURCE_RANK[existing.source]


def fingerprint_skill_paths(resolved: list[tuple[Path, SkillSource]]) -> tuple[tuple[str, int, int], ...]:
    entries: list[tuple[str, int, int]] = []
    for base_path, _source in resolved:
        if not base_path.exists() or not base_path.is_dir():
            continue
        try:
            subdirs = sorted(path for path in base_path.iterdir() if path.is_dir())
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


def _parse_skill_file(skill_file: Path, skill_dir: Path, source: SkillSource) -> SkillSpec | None:
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
        payload = parse_skill_frontmatter(frontmatter_text)
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
    return SkillSpec(name=cfg.name, description=cfg.description, body=body, skill_dir=skill_dir, source=source)


def parse_skill_frontmatter(frontmatter: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for raw_line in frontmatter.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or line.startswith(" "):
            continue
        if ":" not in stripped:
            raise ValueError(f"invalid frontmatter line: {raw_line}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        if key not in {"name", "description", "enabled"}:
            continue
        result[key] = parse_scalar(value.strip())
    return result
