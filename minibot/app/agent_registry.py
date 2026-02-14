from __future__ import annotations

from minibot.core.agents import AgentSpec


class AgentRegistry:
    def __init__(self, specs: list[AgentSpec]) -> None:
        by_name: dict[str, AgentSpec] = {}
        for spec in specs:
            by_name[spec.name] = spec
        self._by_name = by_name

    def all(self) -> list[AgentSpec]:
        return list(self._by_name.values())

    def get(self, name: str) -> AgentSpec | None:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return sorted(self._by_name.keys())

    def is_empty(self) -> bool:
        return not self._by_name

