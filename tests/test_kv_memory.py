from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import KeyValueMemoryConfig
from minibot.adapters.memory.kv_sqlalchemy import KVEntry, SQLAlchemyKeyValueMemory


@pytest_asyncio.fixture()
async def kv_memory(tmp_path: Path) -> SQLAlchemyKeyValueMemory:
    db_path = tmp_path / "kv" / "memory.db"
    config = KeyValueMemoryConfig(
        enabled=True,
        sqlite_url=f"sqlite+aiosqlite:///{db_path}",
        default_limit=10,
        max_limit=50,
    )
    backend = SQLAlchemyKeyValueMemory(config)
    await backend.initialize()
    return backend


@pytest.mark.asyncio
async def test_kv_memory_save_and_get(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    entry = await kv_memory.save_entry(
        owner_id="tenant",
        title="Project Plan",
        data="First draft",
        metadata={"tags": ["plan"]},
        source="notion",
    )

    by_id = await kv_memory.get_entry(owner_id="tenant", entry_id=entry.id)
    assert by_id is not None
    assert by_id.title == "Project Plan"
    assert by_id.metadata["tags"] == ["plan"]

    by_title = await kv_memory.get_entry(owner_id="tenant", title="project plan")
    assert by_title is not None
    assert by_title.id == entry.id


@pytest.mark.asyncio
async def test_kv_memory_update_and_search(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    first = await kv_memory.save_entry(owner_id="tenant", title="Note", data="Alpha")
    updated = await kv_memory.save_entry(owner_id="tenant", title="note", data="Beta")
    assert first.id == updated.id
    assert updated.data == "Beta"

    await kv_memory.save_entry(owner_id="tenant", title="Recipe", data="Chocolate cake")
    result = await kv_memory.search_entries(owner_id="tenant", query="beta", limit=5, offset=0)
    assert result.total == 1
    assert result.entries[0].title == "Note"

    empty = await kv_memory.search_entries(owner_id="tenant", query="missing")
    assert empty.total == 0


@pytest.mark.asyncio
async def test_kv_memory_delete_entry(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    entry = await kv_memory.save_entry(owner_id="tenant", title="Temporary", data="Keep")

    deleted = await kv_memory.delete_entry(owner_id="tenant", entry_id=entry.id)
    assert deleted is True

    missing = await kv_memory.get_entry(owner_id="tenant", entry_id=entry.id)
    assert missing is None


@pytest.mark.asyncio
async def test_kv_memory_delete_by_title_and_missing(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    await kv_memory.save_entry(owner_id="tenant", title="Archive", data="Entry")

    deleted = await kv_memory.delete_entry(owner_id="tenant", title="archive")
    assert deleted is True

    deleted_missing = await kv_memory.delete_entry(owner_id="tenant", title="archive")
    assert deleted_missing is False


@pytest.mark.asyncio
async def test_kv_memory_delete_by_title_removes_single_matching_entry(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    now = datetime.now(timezone.utc)
    older = KVEntry(
        id=uuid4().hex,
        owner_id="tenant",
        title="Archive",
        data="older",
        payload={},
        source=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )
    newer = KVEntry(
        id=uuid4().hex,
        owner_id="tenant",
        title="archive",
        data="newer",
        payload={},
        source=None,
        created_at=now,
        updated_at=now + timedelta(microseconds=1),
        expires_at=None,
    )
    async with kv_memory._session_factory() as session:  # type: ignore[attr-defined]
        session.add_all([older, newer])
        await session.commit()

    deleted = await kv_memory.delete_entry(owner_id="tenant", title="archive")
    assert deleted is True

    remaining = await kv_memory.search_entries(owner_id="tenant", query="archive", limit=10, offset=0)
    assert remaining.total == 1
    assert remaining.entries[0].id == older.id


@pytest.mark.asyncio
async def test_kv_memory_delete_requires_selector(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    with pytest.raises(ValueError):
        await kv_memory.delete_entry(owner_id="tenant")
