from __future__ import annotations

from sqlalchemy import Column, DateTime, String, Text, delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from minibot.adapters.config.schema import MemoryConfig
from minibot.adapters.sqlalchemy_utils import ensure_parent_dir, resolve_sqlite_storage_path
from minibot.shared.datetime_utils import utcnow

Base = declarative_base()


class PendingTurnRecord(Base):
    __tablename__ = "pending_turns"
    event_id = Column(String(64), primary_key=True)
    message_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class PendingTurnStore:
    """Durable marker for an in-flight message turn.

    Written before ``Dispatcher`` hands a message to the LLM pipeline and cleared once that turn
    finishes (success or a handled exception). A row surviving to the next boot means the process
    died mid-turn; the daemon replays it so the reply isn't silently lost. Shares the same SQLite
    database as chat history (``[memory].sqlite_url``) via its own table.
    """

    def __init__(self, config: MemoryConfig) -> None:
        storage_path = resolve_sqlite_storage_path(config.sqlite_url)
        if storage_path:
            ensure_parent_dir(storage_path)
        self._engine: AsyncEngine = create_async_engine(config.sqlite_url, future=True)
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def mark_pending(self, event_id: str, message_json: str) -> None:
        async with self._session_factory() as session:
            stmt = (
                sqlite_insert(PendingTurnRecord)
                .values(event_id=event_id, message_json=message_json, created_at=utcnow())
                .on_conflict_do_nothing()
            )
            await session.execute(stmt)
            await session.commit()

    async def clear_pending(self, event_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(PendingTurnRecord).where(PendingTurnRecord.event_id == event_id))
            await session.commit()

    async def list_pending(self) -> list[tuple[str, str]]:
        async with self._session_factory() as session:
            result = await session.execute(select(PendingTurnRecord.event_id, PendingTurnRecord.message_json))
            return [(str(row[0]), str(row[1])) for row in result.all()]
