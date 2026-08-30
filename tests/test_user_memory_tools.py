from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import KeyValueMemoryConfig
from minibot.adapters.memory.kv_sqlalchemy import SQLAlchemyKeyValueMemory
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.user_memory import build_kv_tools


@pytest_asyncio.fixture()
async def kv_memory(tmp_path: Path) -> SQLAlchemyKeyValueMemory:
    db_path = tmp_path / "kv" / "tools.db"
    backend = SQLAlchemyKeyValueMemory(KeyValueMemoryConfig(enabled=True, sqlite_url=f"sqlite+aiosqlite:///{db_path}"))
    await backend.initialize()
    return backend


def _memory_binding(kv_memory: SQLAlchemyKeyValueMemory):
    return {binding.tool.name: binding for binding in build_kv_tools(kv_memory)}["memory"]


async def _invoke(binding, payload, owner: str | None = "team-alpha"):
    return await binding.handler(payload, ToolContext(owner_id=owner))


@pytest.mark.asyncio
async def test_user_memory_tools_create_search_update_and_delete(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    memory = _memory_binding(kv_memory)
    created = await _invoke(
        memory,
        {"action": "create", "title": "Debt: Darcy", "data": "1000 EUR", "category": "finanzas"},
    )
    assert created["created"] is True

    duplicate = await _invoke(
        memory,
        {"action": "create", "title": "debt: darcy", "data": "duplicate", "category": "finanzas"},
    )
    assert duplicate["error_code"] == "memory:create:duplicate_title"
    assert duplicate["existing_entry"]["id"] == created["id"]

    searched = await _invoke(memory, {"action": "search", "query": "Darcy", "category": "finanzas"})
    assert searched["entries"][0]["id"] == created["id"]

    updated = await _invoke(memory, {"action": "update", "entry_id": created["id"], "data": "900 EUR"})
    assert updated["updated"] is True
    assert updated["title"] == "Debt: Darcy"
    assert updated["data"] == "900 EUR"

    deleted = await _invoke(memory, {"action": "delete", "entry_id": created["id"]})
    assert deleted["deleted"] is True


@pytest.mark.asyncio
async def test_user_memory_list_titles_returns_ids_and_categories(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    memory = _memory_binding(kv_memory)
    await _invoke(
        memory,
        {"action": "create", "title": "Travel Preferences", "data": "Beach", "category": "preferencias"},
    )
    await _invoke(memory, {"action": "create", "title": "Work Setup", "data": "MacBook", "category": "proyectos"})

    listed = await _invoke(memory, {"action": "list_titles", "category": "preferencias"})
    assert listed["total"] == 1
    assert listed["titles"][0]["title"] == "Travel Preferences"
    assert set(listed["titles"][0]) == {"id", "title", "category", "updated_at", "source"}


@pytest.mark.asyncio
async def test_user_memory_requires_categories_and_ids(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    memory = _memory_binding(kv_memory)
    with pytest.raises(ValueError, match="category is required"):
        await _invoke(memory, {"action": "create", "title": "Doc", "data": "text"})
    with pytest.raises(ValueError, match="entry_id must be a non-empty string"):
        await _invoke(memory, {"action": "update", "data": "text"})
