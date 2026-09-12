from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from minibot.adapters.config.schema import SqliteTaskQueueConfig
from minibot.adapters.tasks.retention import TaskRetentionService
from minibot.adapters.tasks.sqlite_store import SQLiteTaskStore


@pytest.mark.asyncio
async def test_start_purges_expired_terminal_records(tmp_path: Path) -> None:
    store = SQLiteTaskStore(SqliteTaskQueueConfig(sqlite_url=f"sqlite+aiosqlite:///{tmp_path / 'tasks.db'}"))
    cutoffs: list[datetime] = []

    async def record_purge(before: datetime) -> int:
        cutoffs.append(before)
        return 0

    store.purge_done = record_purge  # type: ignore[method-assign]
    service = TaskRetentionService(store, retention_seconds=3600)
    before = datetime.now(UTC)
    await service.start()
    await service.stop()

    assert len(cutoffs) == 1
    assert before - timedelta(seconds=3600) <= cutoffs[0] <= datetime.now(UTC) - timedelta(seconds=3599)
