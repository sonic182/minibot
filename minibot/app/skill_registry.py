from __future__ import annotations

from minibot.core.skills import SkillSpec


class SkillRegistry:
    def __init__(self, specs: list[SkillSpec]) -> None:
        by_name: dict[str, SkillSpec] = {}
        for spec in specs:
            by_name[spec.name] = spec
        self._by_name = by_name

    def all(self) -> list[SkillSpec]:
        return list(self._by_name.values())

    def get(self, name: str) -> SkillSpec | None:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return sorted(self._by_name.keys())

    def is_empty(self) -> bool:
        return not self._by_name

    def prompt_catalog(self) -> str:
        if not self._by_name:
            return ""
        lines = ["Available skills (call activate_skill with the exact skill name to load full instructions):"]
        for name in self.names():
            spec = self._by_name[name]
            description = spec.description.strip() or "No description provided."
            lines.append(f"- {name}: {description}")
        return "\n".join(lines)
