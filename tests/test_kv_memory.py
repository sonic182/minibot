from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import KeyValueMemoryConfig
from minibot.adapters.memory.kv_sqlalchemy import SQLAlchemyKeyValueMemory
from minibot.core.memory import KeyValueMemoryFilter


@pytest_asyncio.fixture()
async def kv_memory(tmp_path: Path) -> SQLAlchemyKeyValueMemory:
    db_path = tmp_path / "kv" / "memory.db"
    backend = SQLAlchemyKeyValueMemory(
        KeyValueMemoryConfig(enabled=True, sqlite_url=f"sqlite+aiosqlite:///{db_path}", default_limit=10, max_limit=50)
    )
    await backend.initialize()
    return backend


@pytest.mark.asyncio
async def test_kv_memory_create_duplicate_and_update_by_id(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    created = await kv_memory.create_entry(
        owner_id="tenant",
        title="Debt: Darcy",
        data="1000 EUR",
        metadata={"category": "finanzas"},
        source="user-provided",
    )
    assert created.created is True

    duplicate = await kv_memory.create_entry(
        owner_id="tenant",
        title="debt: darcy",
        data="should not replace",
        metadata={"category": "finanzas"},
    )
    assert duplicate.created is False
    assert duplicate.entry.id == created.entry.id
    assert duplicate.entry.data == "1000 EUR"

    updated = await kv_memory.update_entry(
        owner_id="tenant",
        entry_id=created.entry.id,
        data="900 EUR",
        metadata={"last_updated": "2026-07-20"},
    )
    assert updated is not None
    assert updated.id == created.entry.id
    assert updated.title == "Debt: Darcy"
    assert updated.data == "900 EUR"
    assert updated.metadata == {"category": "finanzas", "last_updated": "2026-07-20"}


@pytest.mark.asyncio
async def test_kv_memory_search_and_list_apply_structured_filters(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    await kv_memory.create_entry(
        owner_id="tenant",
        title="Debt: Darcy",
        data="900 EUR",
        metadata={"category": "finanzas"},
        source="user-provided",
    )
    await kv_memory.create_entry(
        owner_id="tenant",
        title="Athena CRM",
        data="API REST pending",
        metadata={"category": "proyectos"},
        source="user-provided",
    )

    filters = KeyValueMemoryFilter(category="finanzas", source="user-provided")
    searched = await kv_memory.search_entries(owner_id="tenant", query="Darcy", filters=filters)
    listed = await kv_memory.list_entries(owner_id="tenant", filters=filters)

    assert [entry.title for entry in searched.entries] == ["Debt: Darcy"]
    assert [entry.title for entry in listed.entries] == ["Debt: Darcy"]


@pytest.mark.asyncio
async def test_kv_memory_get_and_delete_require_entry_id(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    created = await kv_memory.create_entry(
        owner_id="tenant",
        title="Temporary",
        data="Keep",
        metadata={"category": "otros"},
    )
    fetched = await kv_memory.get_entry(owner_id="tenant", entry_id=created.entry.id)
    assert fetched is not None
    assert await kv_memory.delete_entry(owner_id="tenant", entry_id=created.entry.id) is True
    assert await kv_memory.get_entry(owner_id="tenant", entry_id=created.entry.id) is None

    with pytest.raises(ValueError):
        await kv_memory.get_entry(owner_id="tenant", entry_id="")


@pytest.mark.asyncio
async def test_kv_memory_date_filters(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    created = await kv_memory.create_entry(
        owner_id="tenant",
        title="Current",
        data="Current data",
        metadata={"category": "otros"},
    )
    before = datetime.now(UTC)
    result = await kv_memory.search_entries(
        owner_id="tenant",
        filters=KeyValueMemoryFilter(updated_after=before),
    )
    assert result.entries == []
    assert created.entry.id
