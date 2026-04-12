from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from minibot.app.skill_definitions_loader import (
    fingerprint_skill_paths,
    load_skill_specs,
    resolve_skill_discovery_paths,
)
from minibot.core.skills import SkillSpec


class SkillRegistry:
    def __init__(self, specs: Sequence[SkillSpec] | None = None, paths: list[str] | None = None) -> None:
        self._paths = list(paths) if paths is not None else None
        self._resolved_paths = (
            resolve_skill_discovery_paths(self._paths) if self._paths is not None or specs is None else []
        )
        self._fingerprint = fingerprint_skill_paths(self._resolved_paths)
        self._by_name: dict[str, SkillSpec] = {}
        self._replace_specs(specs if specs is not None else load_skill_specs(self._paths))

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

    def prompt_catalog(self) -> str:
        self.refresh_if_stale()
        if not self._by_name:
            return ""
        lines = ["Available skills (call activate_skill with the exact skill name to load full instructions):"]
        for name in self.names():
            spec = self._by_name[name]
            description = spec.description.strip() or "No description provided."
            lines.append(f"- {name}: {description}")
        return "\n".join(lines)

    def discovery_paths(self) -> list[Path]:
        return [base_path for base_path, _is_project_level in self._resolved_paths]

    def refresh_if_stale(self) -> bool:
        if not self._resolved_paths:
            return False
        fingerprint = fingerprint_skill_paths(self._resolved_paths)
        if fingerprint == self._fingerprint:
            return False
        self._replace_specs(load_skill_specs(self._paths))
        self._fingerprint = fingerprint
        return True

    def _replace_specs(self, specs: Sequence[SkillSpec]) -> None:
        by_name: dict[str, SkillSpec] = {}
        for spec in specs:
            by_name[spec.name] = spec
        self._by_name = by_name
