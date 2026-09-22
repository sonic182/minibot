from __future__ import annotations

import re

import aiosonic
import pytest
import pytest_asyncio

from minibot.adapters.config.schema import HTTPServerConfig, KeyValueMemoryConfig
from minibot.adapters.http import HttpServer, set_nav_entries
from minibot.adapters.memory.kv_sqlalchemy import SQLAlchemyKeyValueMemory
from minibot.extensions.tools.memory import _build_page

TOKEN = "s3cret"
OWNER = "primary"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _csrf_token(body: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', body)
    assert match is not None
    return match.group(1)


@pytest_asyncio.fixture()
async def memory(tmp_path):
    store = SQLAlchemyKeyValueMemory(KeyValueMemoryConfig(sqlite_url=f"sqlite+aiosqlite:///{tmp_path}/kv.db"))
    await store.initialize()
    yield store


@pytest_asyncio.fixture()
async def server(memory: SQLAlchemyKeyValueMemory):
    set_nav_entries([("/memory", "Memory")])
    instance = HttpServer(
        HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, auth_token=TOKEN),
        [("/memory", _build_page(memory, OWNER), ("GET", "POST"))],
    )
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


async def _entry(memory: SQLAlchemyKeyValueMemory, title: str, data: str) -> str:
    created = await memory.create_entry(owner_id=OWNER, title=title, data=data)
    return created.entry.id


@pytest.mark.asyncio
async def test_edit_replaces_data_and_bumps_updated_at(server: HttpServer, memory) -> None:
    entry_id = await _entry(memory, "coffee", "black")
    before = (await memory.get_entry(OWNER, entry_id)).updated_at

    async with aiosonic.HTTPClient() as client:
        page = await client.get(f"http://127.0.0.1:{server.port}/memory", headers=AUTH)
        csrf_token = _csrf_token(await page.text())
        response = await client.post(
            f"http://127.0.0.1:{server.port}/memory",
            data={"action": "update", "id": entry_id, "data": "with milk", "csrf_token": csrf_token},
            headers=AUTH,
        )
        assert response.status_code == 303

    entry = await memory.get_entry(OWNER, entry_id)
    assert entry.data == "with milk"
    assert entry.updated_at > before


@pytest.mark.asyncio
async def test_delete_removes_the_entry(server: HttpServer, memory) -> None:
    entry_id = await _entry(memory, "coffee", "black")

    async with aiosonic.HTTPClient() as client:
        page = await client.get(f"http://127.0.0.1:{server.port}/memory", headers=AUTH)
        csrf_token = _csrf_token(await page.text())
        response = await client.post(
            f"http://127.0.0.1:{server.port}/memory",
            data={"action": "delete", "id": entry_id, "csrf_token": csrf_token},
            headers=AUTH,
        )
        assert response.status_code == 303

    assert await memory.get_entry(OWNER, entry_id) is None


@pytest.mark.asyncio
async def test_page_lists_entries_with_their_controls(server: HttpServer, memory) -> None:
    entry_id = await _entry(memory, "coffee", "black")

    async with aiosonic.HTTPClient() as client:
        response = await client.get(f"http://127.0.0.1:{server.port}/memory", headers=AUTH)
        assert response.status_code == 200
        body = await response.text()

    assert f'data-confirm-value="{entry_id}"' in body
    assert "<dialog" in body


@pytest.mark.asyncio
async def test_search_shows_only_matches_and_keeps_the_query_on_writes(server: HttpServer, memory) -> None:
    await _entry(memory, "coffee", "black")
    tea_id = await _entry(memory, "tea", "green")

    async with aiosonic.HTTPClient() as client:
        page = await client.get(f"http://127.0.0.1:{server.port}/memory?q=GREEN", headers=AUTH)
        body = await page.text()
        response = await client.post(
            f"http://127.0.0.1:{server.port}/memory?q=GREEN",
            data={"action": "update", "id": tea_id, "data": "green tea", "csrf_token": _csrf_token(body)},
            headers=AUTH,
        )

    assert "tea" in body
    assert "coffee" not in body
    assert response.status_code == 303
    assert response.headers["location"] == "/memory?q=GREEN&offset=0"


@pytest.mark.asyncio
async def test_writes_from_a_later_page_return_to_the_first(server: HttpServer, memory) -> None:
    entry_id = await _entry(memory, "coffee", "black")

    async with aiosonic.HTTPClient() as client:
        page = await client.get(f"http://127.0.0.1:{server.port}/memory", headers=AUTH)
        csrf_token = _csrf_token(await page.text())
        response = await client.post(
            f"http://127.0.0.1:{server.port}/memory?q=coffee&offset=50",
            data={"action": "delete", "id": entry_id, "csrf_token": csrf_token},
            headers=AUTH,
        )

    assert response.headers["location"] == "/memory?q=coffee&offset=0"


@pytest.mark.asyncio
async def test_writes_require_auth(server: HttpServer, memory) -> None:
    entry_id = await _entry(memory, "coffee", "black")

    async with aiosonic.HTTPClient() as client:
        response = await client.post(
            f"http://127.0.0.1:{server.port}/memory", data={"action": "delete", "id": entry_id}
        )
        assert response.status_code == 401

    assert await memory.get_entry(OWNER, entry_id) is not None


@pytest.mark.asyncio
async def test_writes_require_csrf_token(server: HttpServer, memory) -> None:
    entry_id = await _entry(memory, "coffee", "black")

    async with aiosonic.HTTPClient() as client:
        response = await client.post(
            f"http://127.0.0.1:{server.port}/memory",
            data={"action": "delete", "id": entry_id},
            headers=AUTH,
        )
        assert response.status_code == 403

    assert await memory.get_entry(OWNER, entry_id) is not None


@pytest.mark.asyncio
async def test_invalid_offset_is_rejected(server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        for offset in ("invalid", "-1", str(2**63)):
            response = await client.get(f"http://127.0.0.1:{server.port}/memory?offset={offset}", headers=AUTH)
            assert response.status_code == 400
