from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import KeyValueMemoryConfig
from minibot.adapters.memory.kv_sqlalchemy import SQLAlchemyKeyValueMemory
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.user_memory import build_kv_tools
from minibot.shared.errors import ToolInputError


@pytest_asyncio.fixture()
async def kv_memory(tmp_path: Path) -> SQLAlchemyKeyValueMemory:
    db_path = tmp_path / "kv" / "tools.db"
    backend = SQLAlchemyKeyValueMemory(KeyValueMemoryConfig(enabled=True, sqlite_url=f"sqlite+aiosqlite:///{db_path}"))
    await backend.initialize()
    return backend


def _bindings(kv_memory: SQLAlchemyKeyValueMemory) -> dict[str, ToolBinding]:
    return {binding.tool.name: binding for binding in build_kv_tools(kv_memory)}


async def _call(bindings: dict[str, ToolBinding], name: str, payload: dict, owner: str | None = "team-alpha"):
    return await bindings[name].handler(payload, ToolContext(owner_id=owner))


def test_memory_is_split_into_one_schema_per_operation(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    tools = {binding.tool.name: binding.tool for binding in build_kv_tools(kv_memory)}

    assert set(tools) == {
        "memory_create",
        "memory_update",
        "memory_get",
        "memory_search",
        "memory_delete",
        "memory_list_titles",
    }
    assert all("action" not in tool.parameters["properties"] for tool in tools.values())


@pytest.mark.asyncio
async def test_user_memory_tools_create_search_update_and_delete(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    tools = _bindings(kv_memory)
    created = await _call(tools, "memory_create", {"title": "Debt: Darcy", "data": "1000 EUR", "category": "finanzas"})
    assert created["created"] is True

    duplicate = await _call(
        tools, "memory_create", {"title": "debt: darcy", "data": "duplicate", "category": "finanzas"}
    )
    assert duplicate["error_code"] == "memory:create:duplicate_title"
    assert duplicate["existing_entry"]["id"] == created["id"]

    searched = await _call(tools, "memory_search", {"query": "Darcy", "category": "finanzas"})
    assert searched["entries"][0]["id"] == created["id"]

    updated = await _call(tools, "memory_update", {"entry_id": searched["entries"][0]["id"], "data": "900 EUR"})
    assert updated["updated"] is True
    assert updated["title"] == "Debt: Darcy"
    assert updated["data"] == "900 EUR"

    deleted = await _call(tools, "memory_delete", {"entry_id": created["id"]})
    assert deleted["deleted"] is True


@pytest.mark.asyncio
async def test_user_memory_list_titles_returns_ids_and_categories(kv_memory: SQLAlchemyKeyValueMemory) -> None:
    tools = _bindings(kv_memory)
    await _call(tools, "memory_create", {"title": "Travel Preferences", "data": "Beach", "category": "preferencias"})
    await _call(tools, "memory_create", {"title": "Work Setup", "data": "MacBook", "category": "proyectos"})

    listed = await _call(tools, "memory_list_titles", {"category": "preferencias"})
    assert listed["total"] == 1
    assert listed["titles"][0]["title"] == "Travel Preferences"
    assert set(listed["titles"][0]) == {"id", "title", "category", "updated_at", "source"}


_CREATE = {"title": "Doc", "data": "text"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "payload", "code", "hint"),
    [
        ("memory_create", _CREATE, "category_required", "finanzas"),
        ("memory_create", {**_CREATE, "category": "deudas"}, "invalid_category", "'deudas'"),
        ("memory_update", {"data": "text"}, "entry_id_required", "memory_list_titles"),
        ("memory_get", {}, "entry_id_required", "memory_list_titles"),
        ("memory_delete", {"entry_id": " "}, "entry_id_required", "memory_list_titles"),
        (
            "memory_create",
            {**_CREATE, "category": "finanzas", "metadata": '{"category": "finanzas"}'},
            "category_in_metadata",
            "top-level",
        ),
        (
            "memory_create",
            {**_CREATE, "category": "finanzas", "metadata": "not json"},
            "invalid_metadata",
            "JSON object",
        ),
        ("memory_update", {"entry_id": "abc"}, "nothing_to_update", "at least one of"),
        ("memory_create", {**_CREATE, "category": "finanzas", "title": None}, "title_required", "non-empty string"),
        ("memory_create", {**_CREATE, "category": "finanzas", "title": "  "}, "title_required", "non-empty string"),
        ("memory_create", {**_CREATE, "category": "finanzas", "data": None}, "data_required", "non-empty string"),
    ],
)
async def test_user_memory_input_errors_carry_actionable_codes(
    kv_memory: SQLAlchemyKeyValueMemory, tool: str, payload: dict[str, str], code: str, hint: str
) -> None:
    with pytest.raises(ToolInputError) as excinfo:
        await _call(_bindings(kv_memory), tool, payload)

    assert excinfo.value.error_code == f"memory:invalid_arguments:{code}"
    assert hint in str(excinfo.value)
