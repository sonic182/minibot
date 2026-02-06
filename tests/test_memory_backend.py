import pytest

from pathlib import Path

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
