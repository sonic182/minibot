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
    config = KeyValueMemoryConfig(
        enabled=True,
        sqlite_url=f"sqlite+aiosqlite:///{db_path}",
    )
    backend = SQLAlchemyKeyValueMemory(config)
    await backend.initialize()
    return backend


def _tool_map(kv_memory: SQLAlchemyKeyValueMemory):
    return {binding.tool.name: binding for binding in build_kv_tools(kv_memory)}


async def _invoke(binding, payload, owner: str | None = "team-alpha"):
    context = ToolContext(owner_id=owner)
    return await binding.handler(payload, context)


@pytest.mark.asyncio
async def test_user_memory_tools_save_get_search(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    tools = _tool_map(kv_memory)
    save_result = await _invoke(
        tools["user_memory_save"],
        {
            "title": "Credentials",
            "data": "API Key",
            "metadata": {"rotated": False},
        },
    )
    assert save_result["owner_id"] == "team-alpha"

    get_result = await _invoke(tools["user_memory_get"], {"entry_id": save_result["id"]})
    assert get_result["data"] == "API Key"

    search_result = await _invoke(tools["user_memory_search"], {"query": "api", "limit": 5})
    assert search_result["total"] == 1
    assert search_result["entries"][0]["title"] == "Credentials"

    delete_result = await _invoke(tools["user_memory_delete"], {"entry_id": save_result["id"]})
    assert delete_result["deleted"] is True

    after_delete = await _invoke(tools["user_memory_get"], {"entry_id": save_result["id"]})
    assert after_delete["message"] == "Entry not found"


@pytest.mark.asyncio
async def test_user_memory_save_requires_owner(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    tools = _tool_map(kv_memory)
    with pytest.raises(ValueError):
        await tools["user_memory_save"].handler({"title": "Doc", "data": "text"}, ToolContext(owner_id=None))


@pytest.mark.asyncio
async def test_user_memory_delete_requires_selector(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    tools = _tool_map(kv_memory)
    with pytest.raises(ValueError):
        await tools["user_memory_delete"].handler({}, ToolContext(owner_id="team-alpha"))
