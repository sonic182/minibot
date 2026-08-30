from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Index, String, Text, delete, func, or_, select, text
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from minibot.adapters.config.schema import KeyValueMemoryConfig
from minibot.adapters.sqlalchemy_utils import ensure_parent_dir, resolve_sqlite_storage_path
from minibot.core.memory import (
    KeyValueCreateResult,
    KeyValueEntry,
    KeyValueMemory,
    KeyValueMemoryFilter,
    KeyValueSearchResult,
)
from minibot.shared.datetime_utils import ensure_utc, utcnow

KVBase = declarative_base()


class KVEntry(KVBase):
    __tablename__ = "kv_memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    data: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    source: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
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
        storage_path = resolve_sqlite_storage_path(config.sqlite_url)
        if storage_path:
            ensure_parent_dir(storage_path)

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

    async def create_entry(
        self,
        owner_id: str,
        title: str,
        data: str,
        metadata: Mapping[str, Any] | None = None,
        source: str | None = None,
        expires_at: datetime | None = None,
    ) -> KeyValueCreateResult:
        if not owner_id:
            raise ValueError("owner_id is required")
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title cannot be empty")
        if not data:
            raise ValueError("data cannot be empty")

        metadata_dict: dict[str, Any] = dict(metadata or {})
        now = utcnow()
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
                return KeyValueCreateResult(entry=self._to_entry(existing_entry), created=False)

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
            return KeyValueCreateResult(entry=self._to_entry(entry), created=True)

    async def update_entry(
        self,
        owner_id: str,
        entry_id: str,
        data: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        source: str | None = None,
        expires_at: datetime | None = None,
    ) -> KeyValueEntry | None:
        if not owner_id:
            raise ValueError("owner_id is required")
        if not entry_id:
            raise ValueError("entry_id is required")

        async with self._session_factory() as session:
            stmt = select(KVEntry).where(KVEntry.owner_id == owner_id, KVEntry.id == entry_id).limit(1)
            result = await session.execute(stmt)
            existing_entry = result.scalars().first()
            if existing_entry is None:
                return None
            if data is not None:
                if not data:
                    raise ValueError("data cannot be empty")
                existing_entry.data = data
            if metadata is not None:
                existing_entry.payload = {**dict(existing_entry.payload or {}), **dict(metadata)}
            if source is not None:
                existing_entry.source = source
            if expires_at is not None:
                existing_entry.expires_at = expires_at
            existing_entry.updated_at = utcnow()
            await session.commit()
            await session.refresh(existing_entry)
            return self._to_entry(existing_entry)

    async def get_entry(
        self,
        owner_id: str,
        entry_id: str,
    ) -> KeyValueEntry | None:
        if not entry_id:
            raise ValueError("entry_id is required")
        async with self._session_factory() as session:
            stmt = select(KVEntry).where(KVEntry.owner_id == owner_id, KVEntry.id == entry_id).limit(1)
            result = await session.execute(stmt)
            entry = result.scalars().first()
            return self._to_entry(entry) if entry else None

    async def delete_entry(
        self,
        owner_id: str,
        entry_id: str,
    ) -> bool:
        if not owner_id:
            raise ValueError("owner_id is required")
        if not entry_id:
            raise ValueError("entry_id is required")

        async with self._session_factory() as session:
            stmt = delete(KVEntry).where(KVEntry.owner_id == owner_id, KVEntry.id == entry_id)
            result = await session.execute(stmt)
            await session.commit()
            return bool(result)

    async def search_entries(
        self,
        owner_id: str,
        query: str | None = None,
        filters: KeyValueMemoryFilter | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> KeyValueSearchResult:
        return await self._query_entries(owner_id, query=query, filters=filters, limit=limit, offset=offset)

    async def list_entries(
        self,
        owner_id: str,
        filters: KeyValueMemoryFilter | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> KeyValueSearchResult:
        return await self._query_entries(owner_id, filters=filters, limit=limit, offset=offset)

    async def _query_entries(
        self,
        owner_id: str,
        query: str | None = None,
        filters: KeyValueMemoryFilter | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> KeyValueSearchResult:
        resolved_limit = self._resolve_limit(limit)
        resolved_offset = max(offset or 0, 0)
        memory_filter = filters or KeyValueMemoryFilter()
        query_filters = self._entry_filters(owner_id, memory_filter)
        normalized_query = query.strip() if query else ""

        async with self._session_factory() as session:
            if normalized_query and self._fts_enabled:
                strict_fts_result = await self._query_entries_fts(
                    session,
                    owner_id=owner_id,
                    query=normalized_query,
                    filters=memory_filter,
                    limit=resolved_limit,
                    offset=resolved_offset,
                    token_joiner="AND",
                )
                if strict_fts_result is not None and strict_fts_result.total > 0:
                    return strict_fts_result

                relaxed_fts_result = await self._query_entries_fts(
                    session,
                    owner_id=owner_id,
                    query=normalized_query,
                    filters=memory_filter,
                    limit=resolved_limit,
                    offset=resolved_offset,
                    token_joiner="OR",
                )
                if relaxed_fts_result is not None and relaxed_fts_result.total > 0:
                    return relaxed_fts_result

            if normalized_query:
                normalized = f"%{normalized_query.lower()}%"
                query_filters.append(
                    or_(
                        func.lower(KVEntry.title).like(normalized),
                        func.lower(KVEntry.data).like(normalized),
                    )
                )

            count_stmt = select(func.count()).select_from(KVEntry).where(*query_filters)
            total_result = await session.execute(count_stmt)
            total = total_result.scalar_one()

            stmt = (
                select(KVEntry)
                .where(*query_filters)
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
        filters: KeyValueMemoryFilter,
        limit: int,
        offset: int,
        token_joiner: str,
    ) -> KeyValueSearchResult | None:
        match_query = self._to_fts_match_query(query, token_joiner=token_joiner)
        if not match_query:
            return None

        filter_sql, filter_params = self._fts_filter_sql(filters)

        count_sql = text(
            "SELECT COUNT(*) AS total "
            "FROM kv_memory_fts f JOIN kv_memory k ON k.rowid = f.rowid "
            "WHERE k.owner_id = :owner_id AND kv_memory_fts MATCH :match_query"
            f"{filter_sql}"
        )
        query_sql = text(
            "SELECT k.id, k.owner_id, k.title, k.data, k.metadata, k.source, k.created_at, k.updated_at, k.expires_at "
            "FROM kv_memory_fts f "
            "JOIN kv_memory k ON k.rowid = f.rowid "
            "WHERE k.owner_id = :owner_id AND kv_memory_fts MATCH :match_query "
            f"{filter_sql} "
            "ORDER BY bm25(kv_memory_fts) ASC, k.updated_at DESC "
            "LIMIT :limit OFFSET :offset"
        )
        params = {"owner_id": owner_id, "match_query": match_query, **filter_params}
        try:
            total_result = await session.execute(count_sql, params)
            total = int(total_result.scalar_one() or 0)
            result = await session.execute(
                query_sql,
                {
                    **params,
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

    @staticmethod
    def _entry_filters(owner_id: str, memory_filter: KeyValueMemoryFilter) -> list[Any]:
        filters: list[Any] = [KVEntry.owner_id == owner_id]
        if memory_filter.category:
            filters.append(KVEntry.payload["category"].as_string() == memory_filter.category)
        if memory_filter.source:
            filters.append(KVEntry.source == memory_filter.source)
        if memory_filter.updated_after:
            filters.append(KVEntry.updated_at >= memory_filter.updated_after)
        if memory_filter.updated_before:
            filters.append(KVEntry.updated_at <= memory_filter.updated_before)
        return filters

    @staticmethod
    def _fts_filter_sql(memory_filter: KeyValueMemoryFilter) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if memory_filter.category:
            clauses.append(" AND json_extract(k.metadata, '$.category') = :category")
            params["category"] = memory_filter.category
        if memory_filter.source:
            clauses.append(" AND k.source = :source")
            params["source"] = memory_filter.source
        if memory_filter.updated_after:
            clauses.append(" AND k.updated_at >= :updated_after")
            params["updated_after"] = memory_filter.updated_after
        if memory_filter.updated_before:
            clauses.append(" AND k.updated_at <= :updated_before")
            params["updated_before"] = memory_filter.updated_before
        return "".join(clauses), params

    def _coerce_datetime(self, value: Any) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value)
            return ensure_utc(parsed)
        return None

    def _coerce_metadata(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _to_fts_match_query(self, query: str, token_joiner: str) -> str:
        tokens = [token for token in query.split() if token]
        if not tokens:
            return ""
        normalized_tokens = [token.replace('"', "").replace("'", "") for token in tokens]
        joiner = " AND " if token_joiner.upper() == "AND" else " OR "
        return joiner.join(f"{token}*" for token in normalized_tokens if token)

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
