from __future__ import annotations

from minibot.core.agents import AgentSpec


class AgentRegistry:
    def __init__(self, specs: list[AgentSpec]) -> None:
        by_name: dict[str, AgentSpec] = {}
        for spec in specs:
            by_name[spec.name] = spec
        self._by_name = by_name

    def replace_all(self, specs: list[AgentSpec]) -> None:
        """Swap the specs in place, keeping this object's identity.

        Token auto-config runs after extensions have registered, so anything already holding the
        registry has to see the updated specs rather than a stale snapshot.
        """
        self._by_name = {spec.name: spec for spec in specs}

    def all(self) -> list[AgentSpec]:
        return list(self._by_name.values())

    def get(self, name: str) -> AgentSpec | None:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return sorted(self._by_name.keys())

    def is_empty(self) -> bool:
        return not self._by_name

    def prompt_roster(self) -> str:
        if not self._by_name:
            return ""
        lines = [
            "Available specialist agents:",
            "Use only these exact agent names for delegation.",
        ]
        for name in self.names():
            spec = self._by_name[name]
            description = spec.description.strip() or "No description provided."
            lines.append(f"- {spec.name}: {description}")
        return "\n".join(lines)
