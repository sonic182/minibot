from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import SqliteTaskQueueConfig
from minibot.adapters.tasks.sqlite_store import SQLiteTaskStore
from minibot.app.task_consumer_service import SQLiteTaskConsumerService
from minibot.core.tasks import TaskRequest, TaskStatus


class _TaskManagerStub:
    """Records spawn calls; ``active()`` drives the consumer's free-slot calculation."""

    def __init__(self, *, active_count: int = 0, spawn_error: Exception | None = None) -> None:
        self.spawned: list[dict[str, Any]] = []
        self.stopped = 0
        self._active_count = active_count
        self._spawn_error = spawn_error

    async def spawn(self, **kwargs: Any) -> None:
        if self._spawn_error is not None:
            raise self._spawn_error
        self.spawned.append(kwargs)

    def active(self) -> list[object]:
        return [object()] * self._active_count

    async def stop(self) -> None:
        self.stopped += 1


def _request(task_id: str) -> TaskRequest:
    return TaskRequest(
        task_id=task_id,
        channel="telegram",
        prompt="do the thing",
        agent_name="data_agent",
        context={"trace_id": task_id},
        chat_id=1,
        user_id=2,
    )


@pytest_asyncio.fixture()
async def store(tmp_path: Path) -> SQLiteTaskStore:
    store = SQLiteTaskStore(
        SqliteTaskQueueConfig(
            sqlite_url=f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}",
            poll_interval_seconds=1,
            lease_timeout_seconds=300,
            batch_size=5,
            max_attempts=3,
        )
    )
    await store.initialize()
    return store


def _consumer(
    store: SQLiteTaskStore,
    task_manager: _TaskManagerStub,
    *,
    max_concurrent_workers: int = 4,
    batch_size: int = 5,
) -> SQLiteTaskConsumerService:
    return SQLiteTaskConsumerService(
        store=store,
        task_manager=cast(Any, task_manager),
        config=SqliteTaskQueueConfig(
            poll_interval_seconds=1,
            lease_timeout_seconds=300,
            batch_size=batch_size,
        ),
        max_concurrent_workers=max_concurrent_workers,
    )


@pytest.mark.asyncio
async def test_run_pending_leases_and_spawns(store: SQLiteTaskStore) -> None:
    await store.create(_request("task-1"))
    manager = _TaskManagerStub()

    assert await _consumer(store, manager).run_pending() == 1

    spawned = manager.spawned[0]
    assert spawned["task_id"] == "task-1"
    assert spawned["channel"] == "telegram"
    assert spawned["prompt"] == "do the thing"
    assert spawned["agent_name"] == "data_agent"
    assert spawned["context"] == {"trace_id": "task-1"}
    assert spawned["chat_id"] == 1
    assert spawned["user_id"] == 2

    leased = await store.get("task-1")
    assert leased is not None
    assert leased.status == TaskStatus.LEASED


@pytest.mark.asyncio
async def test_ack_callback_leaves_task_completion_to_the_manager(store: SQLiteTaskStore) -> None:
    await store.create(_request("task-1"))
    manager = _TaskManagerStub()
    await _consumer(store, manager).run_pending()

    await manager.spawned[0]["ack_cb"]()

    leased = await store.get("task-1")
    assert leased is not None
    assert leased.status == TaskStatus.LEASED
    assert leased.lease_expires_at is not None


@pytest.mark.asyncio
async def test_spawn_failure_retries_the_row_and_frees_the_slot(store: SQLiteTaskStore) -> None:
    await store.create(_request("task-1"))
    manager = _TaskManagerStub(spawn_error=RuntimeError("no fork for you"))
    consumer = _consumer(store, manager, max_concurrent_workers=1)

    await consumer.run_pending()

    requeued = await store.get("task-1")
    assert requeued is not None
    assert requeued.status == TaskStatus.PENDING
    assert requeued.retry_count == 1
    assert requeued.last_error == "no fork for you"

    # The slot was released, so a later poll can still dispatch.
    assert consumer._semaphore.locked() is False


@pytest.mark.asyncio
async def test_no_leases_taken_when_all_workers_are_busy(store: SQLiteTaskStore) -> None:
    await store.create(_request("task-1"))
    manager = _TaskManagerStub(active_count=2)

    assert await _consumer(store, manager, max_concurrent_workers=2).run_pending() == 0

    untouched = await store.get("task-1")
    assert untouched is not None
    assert untouched.status == TaskStatus.PENDING
    assert manager.spawned == []


@pytest.mark.asyncio
async def test_lease_batch_is_capped_by_free_slots(store: SQLiteTaskStore) -> None:
    for index in range(5):
        await store.create(_request(f"task-{index}"))
    manager = _TaskManagerStub(active_count=3)

    # 4 workers, 3 already busy -> only 1 slot free even though batch_size is 5.
    assert await _consumer(store, manager, max_concurrent_workers=4, batch_size=5).run_pending() == 1


@pytest.mark.asyncio
async def test_start_polls_and_stop_shuts_down_the_task_manager(store: SQLiteTaskStore) -> None:
    await store.create(_request("task-1"))
    manager = _TaskManagerStub()
    consumer = _consumer(store, manager)

    await consumer.start()
    for _ in range(50):
        if manager.spawned:
            break
        await asyncio.sleep(0.01)
    await consumer.stop()

    assert [call["task_id"] for call in manager.spawned] == ["task-1"]
    assert manager.stopped == 1


@pytest.mark.asyncio
async def test_poll_failure_does_not_kill_the_loop(store: SQLiteTaskStore) -> None:
    manager = _TaskManagerStub()
    consumer = _consumer(store, manager)
    calls = 0

    async def _boom() -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("poll exploded")

    consumer.run_pending = _boom  # type: ignore[method-assign]
    await consumer.start()
    for _ in range(50):
        if calls:
            break
        await asyncio.sleep(0.01)

    loop_task = consumer._task
    assert loop_task is not None
    assert loop_task.done() is False, "loop died on the first failed poll"

    await consumer.stop()
    assert calls == 1
