from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from minibot.adapters.config.schema import SkillsToolConfig
from minibot.app.skill_definitions_loader import (
    fingerprint_skill_paths,
    load_skill_specs,
    resolve_skill_discovery_paths,
    resolve_skill_write_dir,
)
from minibot.core.skills import SkillSource, SkillSpec


class SkillRegistry:
    """Discovered skills, refreshed from disk on an mtime/size fingerprint.

    ``native`` opts into the skills bundled inside the package. It defaults to ``False`` so that
    constructing a registry over explicit ``paths`` stays exactly that; the daemon turns it on
    from ``[tools.skills] native``.
    """

    def __init__(
        self,
        specs: Sequence[SkillSpec] | None = None,
        paths: list[str] | None = None,
        *,
        native: bool = False,
        native_disabled: Iterable[str] = (),
        write_path: str | None = None,
    ) -> None:
        self._paths = list(paths) if paths is not None else None
        self._native = native
        self._native_disabled = frozenset(native_disabled)
        self._write_path = write_path
        discovers_from_disk = self._paths is not None or specs is None
        self._resolved_paths = self._resolve_paths() if discovers_from_disk else []
        self._fingerprint = fingerprint_skill_paths(self._resolved_paths)
        self._by_name: dict[str, SkillSpec] = {}
        self._replace_specs(specs if specs is not None else self._load_specs())

    @classmethod
    def from_config(cls, config: SkillsToolConfig) -> SkillRegistry:
        """Build the registry ``[tools.skills]`` describes; empty when skills are disabled."""
        if not config.enabled:
            return cls([])
        return cls(
            paths=list(config.paths) or None,
            native=config.native,
            native_disabled=config.disabled_native_skills,
            write_path=config.write_path,
        )

    def _resolve_paths(self) -> list[tuple[Path, SkillSource]]:
        return resolve_skill_discovery_paths(self._paths, native=self._native, write_path=self._write_path)

    def _load_specs(self) -> list[SkillSpec]:
        return load_skill_specs(
            self._paths,
            native=self._native,
            native_disabled=self._native_disabled,
            write_path=self._write_path,
        )

    def write_dir(self) -> Path:
        return resolve_skill_write_dir(self._paths, self._write_path)

    def all(self) -> list[SkillSpec]:
        self.refresh_if_stale()
        return list(self._by_name.values())

    def get(self, name: str) -> SkillSpec | None:
        self.refresh_if_stale()
        return self._by_name.get(name)

    def names(self) -> list[str]:
        self.refresh_if_stale()
        return sorted(self._by_name.keys())

    def is_empty(self) -> bool:
        self.refresh_if_stale()
        return not self._by_name

    def prompt_catalog(self, *, title: str | None = None) -> str:
        self.refresh_if_stale()
        if not self._by_name:
            return ""
        default_title = "Available skills (call activate_skill with the exact skill name to load full instructions):"
        lines = [title or default_title]
        for name in self.names():
            spec = self._by_name[name]
            description = spec.description.strip() or "No description provided."
            lines.append(f"- {name}: {description}")
        return "\n".join(lines)

    def discovery_paths(self) -> list[Path]:
        return [base_path for base_path, _source in self._resolved_paths]

    def refresh_if_stale(self) -> bool:
        if not self._resolved_paths:
            return False
        fingerprint = fingerprint_skill_paths(self._resolved_paths)
        if fingerprint == self._fingerprint:
            return False
        self._replace_specs(self._load_specs())
        self._fingerprint = fingerprint
        return True

    def _replace_specs(self, specs: Sequence[SkillSpec]) -> None:
        by_name: dict[str, SkillSpec] = {}
        for spec in specs:
            by_name[spec.name] = spec
        self._by_name = by_name
