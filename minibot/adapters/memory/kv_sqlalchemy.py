from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Index, String, Text, delete, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import Connection, URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from minibot.adapters.config.schema import KeyValueMemoryConfig
from minibot.core.memory import KeyValueEntry, KeyValueMemory, KeyValueSearchResult

KVBase = declarative_base()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KVEntry(KVBase):
    __tablename__ = "kv_memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    source: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_kv_memory_owner_title_lower",
            func.lower(title),
            owner_id,
        ),
    )


class SQLAlchemyKeyValueMemory(KeyValueMemory):
    def __init__(self, config: KeyValueMemoryConfig) -> None:
        self._config = config
        self._database_url: URL = make_url(config.sqlite_url)
        storage_path = self._resolve_storage_path(config.sqlite_url)
        if storage_path:
            self._ensure_storage_dir(storage_path)

        engine_kwargs: dict[str, Any] = {
            "future": True,
            "echo": config.echo,
        }
        if not self._database_url.drivername.startswith("sqlite"):
            engine_kwargs["pool_size"] = config.pool_size

        self._engine: AsyncEngine = create_async_engine(config.sqlite_url, **engine_kwargs)
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )
        self._fts_enabled = False

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(self._create_schema)
            if self._database_url.drivername.startswith("sqlite"):
                self._fts_enabled = await self._initialize_fts(connection)

    @staticmethod
    def _create_schema(sync_connection: Connection) -> None:
        KVBase.metadata.create_all(sync_connection)

    async def save_entry(
        self,
        owner_id: str,
        title: str,
        data: str,
        metadata: Mapping[str, Any] | None = None,
        source: str | None = None,
        expires_at: datetime | None = None,
    ) -> KeyValueEntry:
        if not owner_id:
            raise ValueError("owner_id is required")
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title cannot be empty")
        if not data:
            raise ValueError("data cannot be empty")

        metadata_dict: dict[str, Any] = dict(metadata or {})
        now = _utcnow()
        async with self._session_factory() as session:
            stmt = (
                select(KVEntry)
                .where(KVEntry.owner_id == owner_id)
                .where(func.lower(KVEntry.title) == normalized_title.lower())
                .limit(1)
            )
            result = await session.execute(stmt)
            existing_entry = result.scalars().first()
            if existing_entry:
                existing_entry.data = data
                existing_entry.payload = metadata_dict
                existing_entry.source = source
                existing_entry.updated_at = now
                existing_entry.expires_at = expires_at
                await session.commit()
                await session.refresh(existing_entry)
                return self._to_entry(existing_entry)

            entry = KVEntry(
                id=uuid4().hex,
                owner_id=owner_id,
                title=normalized_title,
                data=data,
                payload=metadata_dict,
                source=source,
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
            return self._to_entry(entry)

    async def get_entry(
        self,
        owner_id: str,
        entry_id: str | None = None,
        title: str | None = None,
    ) -> KeyValueEntry | None:
        if not entry_id and not title:
            raise ValueError("entry_id or title is required")
        async with self._session_factory() as session:
            stmt = select(KVEntry).where(KVEntry.owner_id == owner_id)
            if entry_id:
                stmt = stmt.where(KVEntry.id == entry_id)
            elif title is not None:
                normalized = title.strip()
                if not normalized:
                    raise ValueError("title cannot be empty")
                stmt = stmt.where(func.lower(KVEntry.title) == normalized.lower())
                stmt = stmt.order_by(KVEntry.updated_at.desc(), KVEntry.id.desc())
            else:
                raise ValueError("title is required when entry_id is not provided")
            stmt = stmt.limit(1)
            result = await session.execute(stmt)
            entry = result.scalars().first()
            return self._to_entry(entry) if entry else None

    async def delete_entry(
        self,
        owner_id: str,
        entry_id: str | None = None,
        title: str | None = None,
    ) -> bool:
        if not owner_id:
            raise ValueError("owner_id is required")
        if not entry_id and not title:
            raise ValueError("entry_id or title is required")

        async with self._session_factory() as session:
            if entry_id:
                target_stmt = select(KVEntry.id).where(KVEntry.owner_id == owner_id, KVEntry.id == entry_id).limit(1)
                target_result = await session.execute(target_stmt)
                target_id = target_result.scalar_one_or_none()
                if target_id is None:
                    return False
            elif title is not None:
                normalized = title.strip()
                if not normalized:
                    raise ValueError("title cannot be empty")
                target_stmt = (
                    select(KVEntry.id)
                    .where(KVEntry.owner_id == owner_id)
                    .where(func.lower(KVEntry.title) == normalized.lower())
                    .order_by(KVEntry.updated_at.desc(), KVEntry.id.desc())
                    .limit(1)
                )
                target_result = await session.execute(target_stmt)
                target_id = target_result.scalar_one_or_none()
                if target_id is None:
                    return False
            else:
                raise ValueError("title is required when entry_id is not provided")

            stmt = delete(KVEntry).where(KVEntry.owner_id == owner_id, KVEntry.id == target_id)
            result = await session.execute(stmt)
            await session.commit()
            return bool(result)

    async def search_entries(
        self,
        owner_id: str,
        query: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> KeyValueSearchResult:
        return await self._query_entries(owner_id, query=query, limit=limit, offset=offset)

    async def list_entries(
        self,
        owner_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> KeyValueSearchResult:
        return await self._query_entries(owner_id, limit=limit, offset=offset)

    async def _query_entries(
        self,
        owner_id: str,
        query: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> KeyValueSearchResult:
        resolved_limit = self._resolve_limit(limit)
        resolved_offset = max(offset or 0, 0)
        filters = [KVEntry.owner_id == owner_id]
        normalized_query = query.strip() if query else ""

        async with self._session_factory() as session:
            if normalized_query and self._fts_enabled:
                fts_result = await self._query_entries_fts(
                    session,
                    owner_id=owner_id,
                    query=normalized_query,
                    limit=resolved_limit,
                    offset=resolved_offset,
                )
                if fts_result is not None:
                    return fts_result

            if normalized_query:
                normalized = f"%{normalized_query.lower()}%"
                filters.append(
                    or_(
                        func.lower(KVEntry.title).like(normalized),
                        func.lower(KVEntry.data).like(normalized),
                    )
                )

            count_stmt = select(func.count()).select_from(KVEntry).where(*filters)
            total_result = await session.execute(count_stmt)
            total = total_result.scalar_one()

            stmt = (
                select(KVEntry)
                .where(*filters)
                .order_by(KVEntry.updated_at.desc())
                .offset(resolved_offset)
                .limit(resolved_limit)
            )
            result = await session.execute(stmt)
            entries = [self._to_entry(row) for row in result.scalars().all()]
            return KeyValueSearchResult(
                entries=entries,
                total=total,
                limit=resolved_limit,
                offset=resolved_offset,
            )

    async def _initialize_fts(self, connection: Any) -> bool:
        try:
            await connection.execute(
                text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS kv_memory_fts USING "
                    "fts5(title, data, content='kv_memory', content_rowid='rowid')"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS kv_memory_ai AFTER INSERT ON kv_memory BEGIN "
                    "INSERT INTO kv_memory_fts(rowid, title, data) VALUES (new.rowid, new.title, new.data); "
                    "END"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS kv_memory_ad AFTER DELETE ON kv_memory BEGIN "
                    "INSERT INTO kv_memory_fts(kv_memory_fts, rowid, title, data) "
                    "VALUES('delete', old.rowid, old.title, old.data); END"
                )
            )
            await connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS kv_memory_au AFTER UPDATE ON kv_memory BEGIN "
                    "INSERT INTO kv_memory_fts(kv_memory_fts, rowid, title, data) "
                    "VALUES('delete', old.rowid, old.title, old.data); "
                    "INSERT INTO kv_memory_fts(rowid, title, data) VALUES (new.rowid, new.title, new.data); "
                    "END"
                )
            )
            await connection.execute(text("INSERT INTO kv_memory_fts(kv_memory_fts) VALUES('rebuild')"))
        except SQLAlchemyError:
            return False
        return True

    async def _query_entries_fts(
        self,
        session: AsyncSession,
        owner_id: str,
        query: str,
        limit: int,
        offset: int,
    ) -> KeyValueSearchResult | None:
        match_query = self._to_fts_match_query(query)
        if not match_query:
            return None

        count_sql = text(
            "SELECT COUNT(*) AS total "
            "FROM kv_memory_fts f JOIN kv_memory k ON k.rowid = f.rowid "
            "WHERE k.owner_id = :owner_id AND kv_memory_fts MATCH :match_query"
        )
        query_sql = text(
            "SELECT k.id, k.owner_id, k.title, k.data, k.metadata, k.source, k.created_at, k.updated_at, k.expires_at "
            "FROM kv_memory_fts f "
            "JOIN kv_memory k ON k.rowid = f.rowid "
            "WHERE k.owner_id = :owner_id AND kv_memory_fts MATCH :match_query "
            "ORDER BY bm25(kv_memory_fts) ASC, k.updated_at DESC "
            "LIMIT :limit OFFSET :offset"
        )
        try:
            total_result = await session.execute(count_sql, {"owner_id": owner_id, "match_query": match_query})
            total = int(total_result.scalar_one() or 0)
            result = await session.execute(
                query_sql,
                {
                    "owner_id": owner_id,
                    "match_query": match_query,
                    "limit": limit,
                    "offset": offset,
                },
            )
        except SQLAlchemyError:
            self._fts_enabled = False
            return None

        entries = [
            KeyValueEntry(
                id=row.id,
                owner_id=row.owner_id,
                title=row.title,
                data=row.data,
                metadata=self._coerce_metadata(row.metadata),
                source=row.source,
                created_at=self._coerce_datetime(row.created_at),
                updated_at=self._coerce_datetime(row.updated_at),
                expires_at=self._coerce_datetime(row.expires_at),
            )
            for row in result.mappings().all()
        ]
        return KeyValueSearchResult(entries=entries, total=total, limit=limit, offset=offset)

    def _coerce_datetime(self, value: Any) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        return None

    def _coerce_metadata(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _to_fts_match_query(self, query: str) -> str:
        tokens = [token for token in query.split() if token]
        if not tokens:
            return ""
        normalized_tokens = [token.replace('"', "").replace("'", "") for token in tokens]
        return " AND ".join(f"{token}*" for token in normalized_tokens if token)

    def _resolve_limit(self, limit: int | None) -> int:
        requested = limit or self._config.default_limit
        return max(1, min(requested, self._config.max_limit))

    def _to_entry(self, model: KVEntry) -> KeyValueEntry:
        return KeyValueEntry(
            id=model.id,
            owner_id=model.owner_id,
            title=model.title,
            data=model.data,
            metadata=dict(model.payload or {}),
            source=model.source,
            created_at=model.created_at,
            updated_at=model.updated_at,
            expires_at=model.expires_at,
        )

    def _resolve_storage_path(self, sqlite_url: str) -> Optional[Path]:
        url = make_url(sqlite_url)
        if url.database and url.drivername.startswith("sqlite") and url.database != ":memory":
            return Path(url.database)
        return None

    def _ensure_storage_dir(self, path: Path) -> None:
        directory = path.parent
        if directory and not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
