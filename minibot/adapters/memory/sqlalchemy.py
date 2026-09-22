from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Column, DateTime, Integer, String, Text, and_, case, column, delete, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from minibot.adapters.config.schema import MemoryConfig
from minibot.adapters.sqlalchemy_utils import (
    ensure_parent_dir,
    fts_match_query,
    like_pattern,
    resolve_sqlite_storage_path,
)
from minibot.core.memory import HistoryPage, MemoryBackend, MemoryEntry, SessionPage, SessionSummary
from minibot.shared.datetime_utils import utcnow

Base = declarative_base()


class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), index=True, nullable=False)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    reasoning = Column(Text, nullable=True)
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
        self._fts_enabled = False

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(self._ensure_reasoning_column)
            self._fts_enabled = await self._initialize_fts(connection)

    @staticmethod
    async def _initialize_fts(connection: Any) -> bool:
        try:
            await connection.execute(
                text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING "
                    "fts5(content, content='messages', content_rowid='id')"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN "
                    "INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content); END"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN "
                    "INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content); END"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN "
                    "INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content); "
                    "INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content); END"
                )
            )
            # Rebuild only when the index is out of step with the table: a fresh index, or one whose
            # first fill was interrupted (SQLite keeps the DDL even when the rebuild fails). The
            # triggers keep it current afterwards, so a large history is not re-indexed every boot.
            indexed = (await connection.execute(text("SELECT count(*) FROM messages_fts_docsize"))).scalar_one()
            stored = (await connection.execute(text("SELECT count(*) FROM messages"))).scalar_one()
            if indexed != stored:
                await connection.execute(text("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')"))
        except SQLAlchemyError:
            return False
        return True

    @staticmethod
    def _ensure_reasoning_column(connection: Any) -> None:
        columns = [row[1] for row in connection.execute(text("PRAGMA table_info(messages)"))]
        if "reasoning" not in columns:
            connection.execute(text("ALTER TABLE messages ADD COLUMN reasoning TEXT"))

    async def append_history(self, session_id: str, role: str, content: str, *, reasoning: str | None = None) -> None:
        async with self._session_factory() as session:
            message = Message(
                session_id=session_id,
                role=role,
                content=content,
                reasoning=reasoning,
                created_at=utcnow(),
            )
            session.add(message)
            await session.commit()

    async def get_history(self, session_id: str, limit: int | None = None) -> Iterable[MemoryEntry]:
        async with self._session_factory() as session:
            stmt = select(Message).where(Message.session_id == session_id).order_by(Message.created_at.desc())
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            messages = result.scalars().all()
            return [_to_entry(message) for message in reversed(messages)]

    async def get_history_page(
        self, session_id: str, *, before_id: int | None = None, query: str | None = None, limit: int = 50
    ) -> HistoryPage:
        stmt = select(Message).where(Message.session_id == session_id)
        if before_id is not None:
            stmt = stmt.where(Message.id < before_id)
        if query and query.strip():
            stmt = stmt.where(self._content_matches(query.strip()))
        stmt = stmt.order_by(Message.id.desc()).limit(limit + 1)
        async with self._session_factory() as session:
            messages = (await session.execute(stmt)).scalars().all()
        entries = [_to_entry(message) for message in messages[:limit]]
        next_before_id = entries[-1].id if len(messages) > limit else None
        return HistoryPage(entries=entries, next_before_id=next_before_id)

    async def count_history(self, session_id: str) -> int:
        async with self._session_factory() as session:
            stmt = select(func.count()).select_from(Message).where(Message.session_id == session_id)
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def list_sessions(
        self, *, query: str | None = None, cursor: str | None = None, limit: int = 50
    ) -> SessionPage:
        last_activity = func.max(Message.created_at)
        stmt = select(
            Message.session_id,
            func.count().label("message_count"),
            last_activity.label("last_activity"),
        ).group_by(Message.session_id)
        normalized_query = query.strip() if query else ""
        if normalized_query:
            match_count = func.sum(case((self._content_matches(normalized_query), 1), else_=0))
            session_matches = func.lower(Message.session_id).like(like_pattern(normalized_query.lower()), escape="\\")
            stmt = stmt.add_columns(match_count.label("match_count")).having(or_(match_count > 0, session_matches))
        if cursor:
            cursor_activity, cursor_session = _parse_session_cursor(cursor)
            stmt = stmt.having(
                or_(
                    last_activity < cursor_activity,
                    and_(last_activity == cursor_activity, Message.session_id < cursor_session),
                )
            )
        stmt = stmt.order_by(last_activity.desc(), Message.session_id.desc()).limit(limit + 1)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).all()
        sessions = [
            SessionSummary(
                session_id=str(row.session_id),
                message_count=int(row.message_count),
                last_activity=cast(datetime, row.last_activity),
                match_count=int(row.match_count) if normalized_query else None,
            )
            for row in rows[:limit]
        ]
        next_cursor = None
        if len(rows) > limit:
            last = sessions[-1]
            next_cursor = f"{last.last_activity.isoformat()}|{last.session_id}"
        return SessionPage(sessions=sessions, next_cursor=next_cursor)

    def _content_matches(self, query: str) -> Any:
        match_query = fts_match_query(query)
        if self._fts_enabled and match_query:
            fts_rows = (
                text("SELECT rowid FROM messages_fts WHERE messages_fts MATCH :match_query")
                .bindparams(match_query=match_query)
                .columns(column("rowid", Integer))
            )
            return Message.id.in_(fts_rows)
        return func.lower(Message.content).like(like_pattern(query.lower()), escape="\\")

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


def _to_entry(message: Message) -> MemoryEntry:
    return MemoryEntry(
        role=str(message.role),
        content=str(message.content),
        created_at=cast(datetime, message.created_at),
        reasoning=cast(str | None, message.reasoning),
        id=cast(int, message.id),
    )


def _parse_session_cursor(cursor: str) -> tuple[datetime, str]:
    """Split a ``SessionPage.next_cursor``. Raises ``ValueError`` on anything else."""
    activity, separator, session_id = cursor.partition("|")
    if not separator or not session_id:
        raise ValueError("malformed session cursor")
    return datetime.fromisoformat(activity), session_id
