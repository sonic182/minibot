from __future__ import annotations

import asyncio
import contextlib
import logging

from minibot.adapters.config.schema import SqliteTaskQueueConfig
from minibot.adapters.tasks.manager import TaskManager
from minibot.adapters.tasks.sqlite_store import SQLiteTaskStore
from minibot.core.tasks import TaskRecord
from minibot.shared.datetime_utils import utcnow


class SQLiteTaskConsumerService:
    """Polls the SQLite task queue and hands leased rows to ``TaskManager``.

    The RabbitMQ consumer counterpart lives in ``adapters/messaging/rabbitmq/service.py``; only one
    of the two runs, selected by ``tasks.backend``.
    """

    def __init__(
        self,
        store: SQLiteTaskStore,
        task_manager: TaskManager,
        config: SqliteTaskQueueConfig,
        max_concurrent_workers: int,
    ) -> None:
        self._store = store
        self._task_manager = task_manager
        self._config = config
        self._max_concurrent_workers = max_concurrent_workers
        self._semaphore = asyncio.Semaphore(max_concurrent_workers)
        self._logger = logging.getLogger("minibot.tasks.sqlite")
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None
        await self._task_manager.stop()

    async def run_pending(self) -> int:
        # Lease only what can start immediately: a row waiting behind a full semaphore would keep
        # ticking toward lease expiry and could be re-leased by the next poll while still queued.
        slots = self._max_concurrent_workers - len(self._task_manager.active())
        if slots <= 0:
            return 0
        records = await self._store.lease_due_tasks(
            now=utcnow(),
            limit=min(self._config.batch_size, slots),
            lease_timeout_seconds=self._config.lease_timeout_seconds,
        )
        for record in records:
            await self._dispatch(record)
        return len(records)

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_pending()
            except Exception:
                self._logger.exception("sqlite task poll failed")
            await self._wait_for_next_iteration()

    async def _wait_for_next_iteration(self) -> None:
        if self._stop_event.is_set():
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop_event.wait(), timeout=self._config.poll_interval_seconds)

    async def _dispatch(self, record: TaskRecord) -> None:
        request = record.request
        await self._semaphore.acquire()

        async def ack_cb() -> None:
            return None

        async def nack_cb() -> None:
            await self._store.retry_task(request.task_id, "redelivery requested")

        try:
            await self._task_manager.spawn(
                task_id=request.task_id,
                channel=request.channel,
                prompt=request.prompt,
                agent_name=request.agent_name,
                context=request.context,
                chat_id=request.chat_id,
                user_id=request.user_id,
                owner_id=request.owner_id,
                limits=request.limits,
                expected_status=record.status,
                lease_token=record.lease_token,
                ack_cb=ack_cb,
                nack_cb=nack_cb,
                semaphore=self._semaphore,
            )
        except Exception as exc:  # noqa: BLE001
            # spawn never started a reader, so nothing else will release the slot.
            self._semaphore.release()
            self._logger.exception("failed to spawn task worker", exc_info=exc, extra={"task_id": request.task_id})
            await self._store.retry_task(request.task_id, str(exc))
