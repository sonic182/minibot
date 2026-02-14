from __future__ import annotations

from datetime import datetime
from typing import Iterable, cast

from sqlalchemy import Column, DateTime, Integer, String, Text, delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from minibot.adapters.sqlalchemy_utils import ensure_parent_dir, resolve_sqlite_storage_path
from minibot.shared.datetime_utils import utcnow
from minibot.core.memory import MemoryBackend, MemoryEntry
from minibot.adapters.config.schema import MemoryConfig


Base = declarative_base()


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), index=True, nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class SQLAlchemyMemoryBackend(MemoryBackend):
    def __init__(self, config: MemoryConfig) -> None:
        self._config = config
        self._storage_path = resolve_sqlite_storage_path(config.sqlite_url)
        if self._storage_path:
            ensure_parent_dir(self._storage_path)

        self._engine: AsyncEngine = create_async_engine(config.sqlite_url, future=True)
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def append_history(self, session_id: str, role: str, content: str) -> None:
        async with self._session_factory() as session:
            message = Message(session_id=session_id, role=role, content=content, created_at=utcnow())
            session.add(message)
            await session.commit()

    async def get_history(self, session_id: str, limit: int | None = None) -> Iterable[MemoryEntry]:
        async with self._session_factory() as session:
            stmt = select(Message).where(Message.session_id == session_id).order_by(Message.created_at.desc())
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            messages = result.scalars().all()
            return [
                MemoryEntry(
                    role=str(message.role),
                    content=str(message.content),
                    created_at=cast(datetime, message.created_at),
                )
                for message in reversed(messages)
            ]

    async def count_history(self, session_id: str) -> int:
        async with self._session_factory() as session:
            stmt = select(func.count()).select_from(Message).where(Message.session_id == session_id)
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def trim_history(self, session_id: str, keep_latest: int) -> int:
        async with self._session_factory() as session:
            if keep_latest <= 0:
                stmt = delete(Message).where(Message.session_id == session_id)
                result = await session.execute(stmt)
                await session.commit()
                return int(getattr(result, "rowcount", 0) or 0)

            stale_ids = (
                select(Message.id)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .offset(keep_latest)
                .subquery()
            )
            stmt = delete(Message).where(Message.id.in_(select(stale_ids.c.id)))
            result = await session.execute(stmt)
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)
