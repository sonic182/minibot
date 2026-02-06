from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Index, String, Text, func, or_, select
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

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(self._create_schema)

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
            else:
                raise ValueError("title is required when entry_id is not provided")
            stmt = stmt.limit(1)
            result = await session.execute(stmt)
            entry = result.scalars().first()
            return self._to_entry(entry) if entry else None

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
        if query:
            normalized = f"%{query.strip().lower()}%"
            filters.append(
                or_(
                    func.lower(KVEntry.title).like(normalized),
                    func.lower(KVEntry.data).like(normalized),
                )
            )

        async with self._session_factory() as session:
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
