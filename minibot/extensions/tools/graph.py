"""Relation-graph tool, opt-in through ``[extensions] modules``.

Deliberately not in ``_bundled_modules``: that tuple is empty for ``entrypoint="worker"``, so a
bundled registration would never reach a spawned task. Arriving via ``settings.extensions.modules``
is what makes the tool available to the daemon, the console and the task workers alike — which also
means no ``ExtensionService`` can be relied on, since ``ExtensionRegistry.start()`` never runs in a
worker. The store creates its schema on first use instead.
"""

from __future__ import annotations

from minibot.adapters.graph.sqlite import DEFAULT_SQLITE_URL, SqliteGraphStore
from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.graph import build_graph_tools


class _GraphStoreService:
    def __init__(self, store: SqliteGraphStore) -> None:
        self._store = store

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        await self._store.close()


def register(mb: ExtensionContext) -> None:
    store = SqliteGraphStore(
        sqlite_url=str(mb.config.get("sqlite_url") or DEFAULT_SQLITE_URL),
        echo=bool(mb.config.get("echo", False)),
    )
    mb.add_tool(build_graph_tools(store))
    if mb.entrypoint != "worker":
        mb.add_service(_GraphStoreService(store))
