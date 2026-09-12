from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta

from minibot.adapters.tasks.sqlite_store import SQLiteTaskStore
from minibot.shared.datetime_utils import utcnow

_PURGE_INTERVAL_SECONDS = 3600


class TaskRetentionService:
    """Periodically purge expired terminal task records."""

    def __init__(self, store: SQLiteTaskStore, retention_seconds: int) -> None:
        self._store = store
        self._retention_seconds = retention_seconds
        self._logger = logging.getLogger("minibot.tasks.retention")
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self._store.initialize()
        self._stop_event.clear()
        await self._purge()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=_PURGE_INTERVAL_SECONDS)
            if not self._stop_event.is_set():
                await self._purge()

    async def _purge(self) -> None:
        try:
            before = utcnow() - timedelta(seconds=self._retention_seconds)
            purged = await self._store.purge_done(before)
            if purged:
                self._logger.info("purged completed tasks", extra={"purged": purged})
        except Exception:
            self._logger.exception("task retention purge failed")
