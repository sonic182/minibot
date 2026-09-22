from pathlib import Path

import pytest

from minibot.adapters.config.schema import MemoryConfig
from minibot.adapters.memory.sqlalchemy import SQLAlchemyMemoryBackend


@pytest.mark.asyncio
async def test_sqlite_backend_creates_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "history.db"
    config = MemoryConfig(sqlite_url=f"sqlite+aiosqlite:///{db_path}")
    backend = SQLAlchemyMemoryBackend(config)
    await backend.initialize()
    assert db_path.exists()


@pytest.mark.asyncio
async def test_sqlite_backend_counts_and_trims_history(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "history.db"
    config = MemoryConfig(sqlite_url=f"sqlite+aiosqlite:///{db_path}")
    backend = SQLAlchemyMemoryBackend(config)
    await backend.initialize()

    session_id = "session-1"
    await backend.append_history(session_id, "user", "a")
    await backend.append_history(session_id, "assistant", "b")
    await backend.append_history(session_id, "user", "c")

    assert await backend.count_history(session_id) == 3
    removed = await backend.trim_history(session_id, keep_latest=2)
    assert removed == 1
    assert await backend.count_history(session_id) == 2

    history = list(await backend.get_history(session_id, limit=10))
    assert [entry.content for entry in history] == ["b", "c"]


async def _backend(tmp_path: Path) -> SQLAlchemyMemoryBackend:
    backend = SQLAlchemyMemoryBackend(MemoryConfig(sqlite_url=f"sqlite+aiosqlite:///{tmp_path}/history.db"))
    await backend.initialize()
    return backend


@pytest.mark.asyncio
async def test_history_page_walks_older_messages_by_cursor(tmp_path: Path) -> None:
    backend = await _backend(tmp_path)
    for index in range(5):
        await backend.append_history("web:1", "user", f"m{index}")

    seen: list[str] = []
    before_id = None
    while True:
        page = await backend.get_history_page("web:1", before_id=before_id, limit=2)
        seen.extend(entry.content for entry in page.entries)
        if page.next_before_id is None:
            break
        before_id = page.next_before_id

    assert seen == ["m4", "m3", "m2", "m1", "m0"]


@pytest.mark.parametrize("fts_enabled", [True, False])
@pytest.mark.asyncio
async def test_history_search_matches_content_case_insensitively(tmp_path: Path, fts_enabled: bool) -> None:
    backend = await _backend(tmp_path)
    backend._fts_enabled = fts_enabled
    await backend.append_history("telegram:42", "user", "Pizza on web:1 tonight")
    await backend.append_history("telegram:42", "assistant", "sure")
    await backend.append_history("web:1", "user", "no food here")

    page = await backend.get_history_page("telegram:42", query="pizza")
    assert [entry.content for entry in page.entries] == ["Pizza on web:1 tonight"]
    # Punctuation that means something to FTS5 is searched as text, not parsed as syntax.
    assert len((await backend.get_history_page("telegram:42", query="web:1")).entries) == 1

    sessions = (await backend.list_sessions(query="PIZZA")).sessions
    assert [(session.session_id, session.match_count) for session in sessions] == [("telegram:42", 1)]


@pytest.mark.asyncio
async def test_existing_history_is_indexed_and_trim_leaves_the_index(tmp_path: Path) -> None:
    backend = await _backend(tmp_path)
    await backend.append_history("web:1", "user", "remember the milk")
    reopened = await _backend(tmp_path)
    assert len((await reopened.get_history_page("web:1", query="milk")).entries) == 1

    await reopened.trim_history("web:1", keep_latest=0)
    assert (await reopened.list_sessions(query="milk")).sessions == []


@pytest.mark.asyncio
async def test_sessions_page_by_cursor_and_match_session_ids(tmp_path: Path) -> None:
    backend = await _backend(tmp_path)
    for session_id in ("telegram:1", "telegram:2", "web:1"):
        await backend.append_history(session_id, "user", "hello")

    first = await backend.list_sessions(limit=2)
    assert [session.session_id for session in first.sessions] == ["web:1", "telegram:2"]
    rest = await backend.list_sessions(limit=2, cursor=first.next_cursor)
    assert [session.session_id for session in rest.sessions] == ["telegram:1"]
    assert rest.next_cursor is None

    by_id = await backend.list_sessions(query="TELEGRAM")
    assert [session.session_id for session in by_id.sessions] == ["telegram:2", "telegram:1"]
    with pytest.raises(ValueError):
        await backend.list_sessions(cursor="garbage")
