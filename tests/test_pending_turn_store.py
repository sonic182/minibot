from pathlib import Path

import pytest

from minibot.adapters.config.schema import MemoryConfig
from minibot.adapters.memory.pending_turns import PendingTurnStore


@pytest.mark.asyncio
async def test_pending_turn_store_creates_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "history.db"
    config = MemoryConfig(sqlite_url=f"sqlite+aiosqlite:///{db_path}")
    store = PendingTurnStore(config)
    await store.initialize()
    assert db_path.exists()


@pytest.mark.asyncio
async def test_pending_turn_store_mark_list_clear_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "history.db"
    config = MemoryConfig(sqlite_url=f"sqlite+aiosqlite:///{db_path}")
    store = PendingTurnStore(config)
    await store.initialize()

    await store.mark_pending("event-1", '{"channel": "telegram", "text": "hi"}')
    await store.mark_pending("event-2", '{"channel": "telegram", "text": "there"}')

    pending = await store.list_pending()
    assert dict(pending) == {
        "event-1": '{"channel": "telegram", "text": "hi"}',
        "event-2": '{"channel": "telegram", "text": "there"}',
    }

    await store.clear_pending("event-1")
    remaining = await store.list_pending()
    assert dict(remaining) == {"event-2": '{"channel": "telegram", "text": "there"}'}


@pytest.mark.asyncio
async def test_pending_turn_store_clear_missing_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "history.db"
    config = MemoryConfig(sqlite_url=f"sqlite+aiosqlite:///{db_path}")
    store = PendingTurnStore(config)
    await store.initialize()

    await store.clear_pending("does-not-exist")
    assert await store.list_pending() == []
