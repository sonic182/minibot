from __future__ import annotations

import json
from typing import Any

import pytest

from minibot.adapters.config.schema import RabbitMQConsumerConfig, SqliteTaskQueueConfig
from minibot.adapters.messaging.rabbitmq.service import RabbitMQConsumerService
from minibot.adapters.tasks.sqlite_store import SQLiteTaskStore
from minibot.app.event_bus import EventBus
from minibot.core.tasks import TaskRequest, TaskStatus
from minibot.shared.datetime_utils import utcnow


class _TaskManagerStub:
    def __init__(self) -> None:
        self.spawned: list[dict[str, Any]] = []

    async def spawn(self, **kwargs: Any) -> bool:
        self.spawned.append(kwargs)
        return True

    async def stop(self) -> None:
        return None


class _Message:
    def __init__(self, body: dict[str, Any], *, redelivered: bool) -> None:
        self.body = json.dumps(body).encode()
        self.redelivered = redelivered
        self.acknowledged = False
        self.nacked = False

    async def ack(self) -> None:
        self.acknowledged = True

    async def nack(self, *, requeue: bool) -> None:
        self.nacked = True


def _request(task_id: str) -> TaskRequest:
    return TaskRequest(task_id=task_id, channel="console", prompt="do the thing", chat_id=1, user_id=2)


@pytest.mark.asyncio
async def test_redelivered_running_task_replaces_its_execution_lease(tmp_path) -> None:
    store = SQLiteTaskStore(SqliteTaskQueueConfig(sqlite_url=f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"))
    await store.initialize()
    request = _request("task-1")
    await store.create(request)
    original_token = await store.claim_execution(
        request.task_id,
        expected_status=TaskStatus.PENDING,
        lease_token=None,
        lease_timeout_seconds=30,
    )
    assert original_token is not None
    manager = _TaskManagerStub()
    service = RabbitMQConsumerService(
        RabbitMQConsumerConfig(),
        EventBus(),
        store,
        manager,
    )
    message = _Message(
        {
            "task_id": request.task_id,
            "channel": request.channel,
            "prompt": request.prompt,
            "chat_id": request.chat_id,
            "user_id": request.user_id,
        },
        redelivered=True,
    )

    await service._dispatch(message)  # type: ignore[arg-type]

    assert message.acknowledged is False
    assert manager.spawned[0]["expected_status"] == TaskStatus.RUNNING
    assert manager.spawned[0]["lease_token"] == original_token
    assert manager.spawned[0]["replace_lease"] is True


@pytest.mark.asyncio
async def test_redelivered_leased_task_is_claimed_after_consumer_crash(tmp_path) -> None:
    store = SQLiteTaskStore(SqliteTaskQueueConfig(sqlite_url=f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"))
    await store.initialize()
    request = _request("task-1")
    await store.create(request)
    leased = await store.lease_due_tasks(now=utcnow(), limit=1, lease_timeout_seconds=30)
    assert leased[0].lease_token is not None
    manager = _TaskManagerStub()
    service = RabbitMQConsumerService(RabbitMQConsumerConfig(), EventBus(), store, manager)
    message = _Message(
        {"task_id": request.task_id, "channel": request.channel, "prompt": request.prompt},
        redelivered=True,
    )

    await service._dispatch(message)  # type: ignore[arg-type]

    assert message.acknowledged is False
    assert manager.spawned[0]["expected_status"] == TaskStatus.LEASED
    assert manager.spawned[0]["lease_token"] == leased[0].lease_token
    assert manager.spawned[0]["replace_lease"] is True
