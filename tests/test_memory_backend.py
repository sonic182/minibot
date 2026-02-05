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
