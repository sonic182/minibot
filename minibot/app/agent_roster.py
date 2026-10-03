"""Re-read agent definitions and swap them in.

Reload is deliberately small: load, derive limits from the cache, diff, then swap. Everything that
can fail — a bad file, a name collision, a ceiling violation — fails during the load, before the
registry is touched, so a rejected reload leaves the running roster exactly as it was.
"""

from __future__ import annotations

import asyncio
import dataclasses

from minibot.app.agent_definitions_loader import load_active_agent_specs
from minibot.app.agent_registry import AgentRegistry
from minibot.app.token_limits_autoconfig import apply_cached_token_limits
from minibot.config.schema import Settings
from minibot.core.agents import AgentDefinitionReader, AgentSpec


@dataclasses.dataclass(frozen=True)
class AgentRosterChange:
    """What a reload produced. ``names`` is the roster after the swap."""

    names: list[str]
    added: list[str]
    removed: list[str]
    updated: list[str]


async def reload_agent_roster(
    *, settings: Settings, registry: AgentRegistry, reader: AgentDefinitionReader
) -> AgentRosterChange:
    """Load the active definitions and swap them into ``registry`` in place.

    ``replace_all`` keeps the registry object, so anything already holding it — the delegation tool,
    the prompt service, the task manager — sees the new specs without further wiring.
    """
    candidate = await asyncio.to_thread(load_active_agent_specs, settings, reader=reader)
    previous = {spec.name: spec.revision for spec in registry.all()}
    candidate = apply_cached_token_limits(settings, candidate)
    change = diff_agent_roster(previous, candidate)
    registry.replace_all(candidate)
    return change


def diff_agent_roster(previous: dict[str, str | None], candidate: list[AgentSpec]) -> AgentRosterChange:
    current = {spec.name: spec.revision for spec in candidate}
    return AgentRosterChange(
        names=sorted(current),
        added=sorted(set(current) - set(previous)),
        removed=sorted(set(previous) - set(current)),
        updated=sorted(name for name in set(current) & set(previous) if current[name] != previous[name]),
    )
