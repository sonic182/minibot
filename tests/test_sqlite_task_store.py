from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import SqliteTaskQueueConfig
from minibot.adapters.tasks.sqlite_store import SQLiteTaskStore
from minibot.core.tasks import TaskRequest, TaskStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _request(task_id: str, prompt: str = "do the thing") -> TaskRequest:
    return TaskRequest(
        task_id=task_id,
        channel="telegram",
        prompt=prompt,
        agent_name="data_agent",
        context={"trace_id": task_id},
        chat_id=1,
        user_id=2,
    )


@pytest_asyncio.fixture()
async def task_store(tmp_path: Path) -> SQLiteTaskStore:
    config = SqliteTaskQueueConfig(
        sqlite_url=f"sqlite+aiosqlite:///{tmp_path / 'tasks' / 'tasks.db'}",
        poll_interval_seconds=1,
        lease_timeout_seconds=5,
        batch_size=5,
        max_attempts=3,
        echo=False,
    )
    store = SQLiteTaskStore(config)
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_create_lease_and_mark_done(task_store: SQLiteTaskStore) -> None:
    await task_store.create(_request("task-1"))

    leased = await task_store.lease_due_tasks(now=_utcnow(), limit=10, lease_timeout_seconds=30)
    assert [record.request.task_id for record in leased] == ["task-1"]
    assert leased[0].status == TaskStatus.LEASED
    assert leased[0].request.prompt == "do the thing"
    assert leased[0].request.agent_name == "data_agent"
    assert leased[0].request.context == {"trace_id": "task-1"}
    assert leased[0].request.chat_id == 1
    assert leased[0].request.user_id == 2

    await task_store.mark_done("task-1")
    stored = await task_store.get("task-1")
    assert stored is not None
    assert stored.status == TaskStatus.DONE
    assert stored.lease_expires_at is None


@pytest.mark.asyncio
async def test_leased_task_is_not_handed_out_twice(task_store: SQLiteTaskStore) -> None:
    await task_store.create(_request("task-1"))
    now = _utcnow()

    first = await task_store.lease_due_tasks(now=now, limit=10, lease_timeout_seconds=300)
    second = await task_store.lease_due_tasks(now=now, limit=10, lease_timeout_seconds=300)

    assert [record.request.task_id for record in first] == ["task-1"]
    assert second == []


@pytest.mark.asyncio
async def test_concurrent_leases_are_exclusive(task_store: SQLiteTaskStore) -> None:
    task_ids = {f"task-{index}" for index in range(6)}
    for task_id in sorted(task_ids):
        await task_store.create(_request(task_id))

    now = _utcnow()
    left, right = await asyncio.gather(
        task_store.lease_due_tasks(now=now, limit=6, lease_timeout_seconds=300),
        task_store.lease_due_tasks(now=now, limit=6, lease_timeout_seconds=300),
    )

    left_ids = [record.request.task_id for record in left]
    right_ids = [record.request.task_id for record in right]
    claimed = left_ids + right_ids

    assert len(claimed) == len(set(claimed)), "a task was leased by both callers"
    assert set(claimed) == task_ids


@pytest.mark.asyncio
async def test_expired_lease_becomes_claimable_again(task_store: SQLiteTaskStore) -> None:
    await task_store.create(_request("task-1"))
    now = _utcnow()

    first = await task_store.lease_due_tasks(now=now, limit=1, lease_timeout_seconds=1)
    assert [record.request.task_id for record in first] == ["task-1"]

    second = await task_store.lease_due_tasks(
        now=now + timedelta(seconds=5),
        limit=1,
        lease_timeout_seconds=1,
    )
    assert [record.request.task_id for record in second] == ["task-1"]


@pytest.mark.asyncio
async def test_retry_requeues_until_attempts_are_exhausted(task_store: SQLiteTaskStore) -> None:
    await task_store.create(_request("task-1"))
    await task_store.lease_due_tasks(now=_utcnow(), limit=1, lease_timeout_seconds=300)

    assert await task_store.retry_task("task-1", error="spawn failed") == TaskStatus.PENDING
    requeued = await task_store.get("task-1")
    assert requeued is not None
    assert requeued.status == TaskStatus.PENDING
    assert requeued.retry_count == 1
    assert requeued.last_error == "spawn failed"
    assert requeued.lease_expires_at is None

    assert await task_store.retry_task("task-1", error="again") == TaskStatus.PENDING
    assert await task_store.retry_task("task-1", error="fatal") == TaskStatus.FAILED

    failed = await task_store.get("task-1")
    assert failed is not None
    assert failed.status == TaskStatus.FAILED
    assert failed.retry_count == 3
    assert failed.last_error == "fatal"


@pytest.mark.asyncio
async def test_retry_missing_task_returns_none(task_store: SQLiteTaskStore) -> None:
    assert await task_store.retry_task("nope") is None


@pytest.mark.asyncio
async def test_purge_done_only_removes_completed_rows(task_store: SQLiteTaskStore) -> None:
    for task_id in ("done-1", "pending-1", "failed-1"):
        await task_store.create(_request(task_id))
    await task_store.mark_done("done-1")
    await task_store.mark_failed("failed-1", error="boom")

    purged = await task_store.purge_done(_utcnow() + timedelta(seconds=1))

    assert purged == 1
    assert await task_store.get("done-1") is None
    assert await task_store.get("pending-1") is not None
    assert await task_store.get("failed-1") is not None


@pytest.mark.asyncio
async def test_purge_done_keeps_rows_newer_than_cutoff(task_store: SQLiteTaskStore) -> None:
    await task_store.create(_request("task-1"))
    await task_store.mark_done("task-1")

    purged = await task_store.purge_done(_utcnow() - timedelta(hours=1))

    assert purged == 0
    assert await task_store.get("task-1") is not None


@pytest.mark.asyncio
async def test_tasks_are_leased_fifo(task_store: SQLiteTaskStore) -> None:
    for task_id in ("task-1", "task-2", "task-3"):
        await task_store.create(_request(task_id))

    leased = await task_store.lease_due_tasks(now=_utcnow(), limit=2, lease_timeout_seconds=300)

    assert [record.request.task_id for record in leased] == ["task-1", "task-2"]


@pytest.mark.asyncio
async def test_producer_enqueue_creates_a_pending_row(task_store: SQLiteTaskStore) -> None:
    from minibot.adapters.tasks.sqlite_store import SQLiteTaskProducer

    await SQLiteTaskProducer(task_store).enqueue(_request("task-1"))

    stored = await task_store.get("task-1")
    assert stored is not None
    assert stored.status == TaskStatus.PENDING
    assert stored.request == _request("task-1")
