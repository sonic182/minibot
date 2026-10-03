"""Validated, serialized writes of model-authored agent definitions.

The service is the only place that turns a proposed definition into a file. It checks the name,
parses the definition, applies the owner's ceiling, and only then persists — so an unauthorized
definition is never written, and a written one is always loadable. The registry refresh is a
callback because the swap belongs to the reload path, not to storage.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from minibot.app.agent_definitions_loader import AGENT_NAME_RE, load_agent_specs, parse_agent_definition
from minibot.app.agent_roster import AgentRosterChange
from minibot.app.managed_agent_policy import ManagedAgentPolicy
from minibot.config.schema import Settings
from minibot.core.agents import AgentSpec, ManagedAgentStore

_LOGGER = logging.getLogger("minibot.agent_management")


@dataclasses.dataclass(frozen=True)
class AgentManagementOutcome:
    """Structured result of one management operation."""

    ok: bool
    action: str
    name: str | None = None
    names: list[str] = dataclasses.field(default_factory=list)
    added: list[str] = dataclasses.field(default_factory=list)
    removed: list[str] = dataclasses.field(default_factory=list)
    updated: list[str] = dataclasses.field(default_factory=list)
    error: str | None = None

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "action": self.action,
            "names": list(self.names),
            "added": list(self.added),
            "removed": list(self.removed),
            "updated": list(self.updated),
        }
        if self.name is not None:
            payload["name"] = self.name
        if self.error is not None:
            payload["error"] = self.error
        return payload


class AgentManagementService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: ManagedAgentStore,
        refresh: Callable[[], Awaitable[AgentRosterChange]] | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._refresh = refresh
        self._lock = asyncio.Lock()

    @property
    def directory(self) -> Path:
        return self._store.directory

    def list_names(self) -> list[str]:
        return self._store.list_names()

    async def reload(self) -> AgentManagementOutcome:
        """Re-read definitions. ``ok`` is false when the reload could not be applied."""
        async with self._lock:
            return await self._finish("reload", None)

    async def create(self, *, name: str, content: str) -> AgentManagementOutcome:
        async with self._lock:
            return await self._write(name=name, content=content, replacing=False)

    async def update(self, *, name: str, content: str) -> AgentManagementOutcome:
        async with self._lock:
            return await self._write(name=name, content=content, replacing=True)

    async def delete(self, *, name: str) -> AgentManagementOutcome:
        async with self._lock:
            try:
                if not self._store.exists(name):
                    return _failure("delete", name, f"managed agent '{name}' does not exist")
                self._store.delete(name)
            except (ValueError, OSError) as exc:
                return _failure("delete", name, str(exc))
            return await self._finish("delete", name)

    async def _write(self, *, name: str, content: str, replacing: bool) -> AgentManagementOutcome:
        action = "update" if replacing else "create"
        try:
            self._authorize(name=name, content=content)
            exists = self._store.exists(name)
            if replacing and not exists:
                return _failure(action, name, f"managed agent '{name}' does not exist; use create_agent")
            if not replacing and exists:
                return _failure(action, name, f"managed agent '{name}' already exists; use update_agent")
            self._store.write(name, content)
        except (ValueError, OSError) as exc:
            return _failure(action, name, str(exc))
        return await self._finish(action, name)

    async def _finish(self, action: str, name: str | None) -> AgentManagementOutcome:
        change: AgentRosterChange | None = None
        if self._refresh is not None:
            try:
                change = await self._refresh()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("agent roster refresh failed", exc_info=True)
                saved = f"'{name}' was {'deleted' if action == 'delete' else 'saved'} but " if name is not None else ""
                return AgentManagementOutcome(
                    ok=False,
                    action=action,
                    name=name,
                    error=(
                        f"{saved}the agent roster could not be refreshed: {exc}. "
                        "Fix the reported error before retrying the reload or restarting."
                    ),
                )
        return AgentManagementOutcome(
            ok=True,
            action=action,
            name=name,
            names=change.names if change is not None else self.list_names(),
            added=change.added if change is not None else [],
            removed=change.removed if change is not None else [],
            updated=change.updated if change is not None else [],
        )

    def _authorize(self, *, name: str, content: str) -> AgentSpec:
        if not AGENT_NAME_RE.fullmatch(name):
            raise ValueError(f"agent name '{name}' must match {AGENT_NAME_RE.pattern}")
        source_path = self._store.directory / f"{name}.md"
        spec = parse_agent_definition(source_path=source_path, text=content, strict_name=True)
        if spec is None:
            raise ValueError(f"{source_path}: a managed agent cannot set enabled = false")
        if spec.name != name:
            raise ValueError(f"{source_path}: frontmatter name '{spec.name}' must match the requested name '{name}'")
        ManagedAgentPolicy.from_settings(self._settings).authorize(spec)
        owner_specs = load_agent_specs(self._settings.orchestration.directory)
        if any(owner.name == name for owner in owner_specs):
            raise ValueError(f"managed agent '{name}' collides with an owner-authored agent; choose another name")
        return spec


def _failure(action: str, name: str, error: str) -> AgentManagementOutcome:
    return AgentManagementOutcome(ok=False, action=action, name=name, error=error)
