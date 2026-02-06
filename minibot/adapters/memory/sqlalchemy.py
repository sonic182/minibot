from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, cast

from sqlalchemy import Column, DateTime, Integer, String, Text, delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from minibot.core.memory import MemoryBackend, MemoryEntry
from minibot.adapters.config.schema import MemoryConfig


Base = declarative_base()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), index=True, nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class SQLAlchemyMemoryBackend(MemoryBackend):
    def __init__(self, config: MemoryConfig) -> None:
        self._config = config
        self._storage_path = self._resolve_storage_path(config.sqlite_url)
        if self._storage_path:
            self._ensure_storage_dir(self._storage_path)

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
            message = Message(session_id=session_id, role=role, content=content, created_at=_utcnow())
            session.add(message)
            await session.commit()

    async def get_history(self, session_id: str, limit: int = 32) -> Iterable[MemoryEntry]:
        async with self._session_factory() as session:
            stmt = (
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
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

    def _resolve_storage_path(self, sqlite_url: str) -> Optional[Path]:
        url = make_url(sqlite_url)
        if url.database and url.drivername.startswith("sqlite") and url.database != ":memory":
            return Path(url.database)
        return None

    def _ensure_storage_dir(self, path: Path) -> None:
        directory = path.parent
        if directory and not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
