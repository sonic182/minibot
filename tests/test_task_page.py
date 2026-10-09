from __future__ import annotations

import aiosonic
import pytest
import pytest_asyncio

from minibot.adapters.config.schema import HTTPServerConfig, SqliteTaskQueueConfig
from minibot.adapters.http import HttpServer, set_nav_entries
from minibot.adapters.tasks.sqlite_store import SQLiteTaskStore
from minibot.app.event_bus import EventBus
from minibot.app.tasks.manager import TaskManager
from minibot.core.tasks import TaskRequest, TaskStatus
from minibot.extensions.services._task_page import build_task_page

TOKEN = "s3cret"
OWNER = "primary"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest_asyncio.fixture()
async def store(tmp_path):
    instance = SQLiteTaskStore(SqliteTaskQueueConfig(sqlite_url=f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"))
    await instance.initialize()
    yield instance


@pytest_asyncio.fixture()
async def server(store: SQLiteTaskStore):
    manager = TaskManager(event_bus=EventBus(), worker_timeout_seconds=1.0, worker_target=lambda _pipe: None)
    set_nav_entries([("/tasks", "Tasks")])
    instance = HttpServer(
        HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, auth_token=TOKEN),
        [("/tasks", build_task_page(store, manager, OWNER), ("GET", "POST"))],
    )
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.mark.asyncio
async def test_lists_shows_and_cancels_an_active_task(server: HttpServer, store: SQLiteTaskStore) -> None:
    await store.create(TaskRequest(task_id="task-abc-123", channel="web", prompt="revisa los logs", agent_name="ops"))
    base = f"http://127.0.0.1:{server.port}/tasks"

    async with aiosonic.HTTPClient() as client:
        listing = await (await client.get(base, headers=AUTH)).text()
        detail = await (await client.get(f"{base}?id=task-abc-123", headers=AUTH)).text()
        response = await client.post(
            base,
            headers={**AUTH, "Content-Type": "application/x-www-form-urlencoded"},
            data="action=cancel&id=task-abc-123",
        )
        assert response.status_code == 303
        after = await (await client.get(base, headers=AUTH)).text()

    assert "revisa los logs" in listing
    assert "1 active" in listing
    assert "queued" in detail
    record = await store.get("task-abc-123", OWNER)
    assert record is not None and record.status == TaskStatus.CANCELLED
    assert "no task running right now" in after
