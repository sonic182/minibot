from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text, and_, or_, select, update
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from minibot.adapters.config.schema import ScheduledPromptsConfig
from minibot.core.jobs import (
    PromptRole,
    ScheduledPrompt,
    ScheduledPromptCreate,
    ScheduledPromptRepository,
    ScheduledPromptStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return _ensure_utc(value)


Base = declarative_base()


class ScheduledPromptModel(Base):
    __tablename__ = "scheduled_prompts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=PromptRole.USER.value)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    metadata_payload: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class SQLAlchemyScheduledPromptStore(ScheduledPromptRepository):
    def __init__(self, config: ScheduledPromptsConfig) -> None:
        self._config = config
        self._database_url = make_url(config.sqlite_url)
        storage_path = self._resolve_storage_path(config.sqlite_url)
        if storage_path:
            self._ensure_storage_dir(storage_path)

        engine_kwargs: dict[str, Any] = {"future": True, "echo": config.echo}
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
    def _create_schema(connection: Connection) -> None:
        Base.metadata.create_all(connection)

    async def create(self, prompt: ScheduledPromptCreate) -> ScheduledPrompt:
        metadata = dict(prompt.metadata or {})
        async with self._session_factory() as session:
            model = ScheduledPromptModel(
                id=uuid4().hex,
                owner_id=prompt.owner_id,
                channel=prompt.channel,
                chat_id=prompt.chat_id,
                user_id=prompt.user_id,
                role=prompt.role.value,
                content=prompt.text,
                status=ScheduledPromptStatus.PENDING.value,
                run_at=_ensure_utc(prompt.run_at),
                retry_count=0,
                max_attempts=prompt.max_attempts,
                metadata_payload=metadata,
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return self._to_domain(model)

    async def lease_due_jobs(
        self,
        *,
        now: datetime,
        limit: int,
        lease_timeout_seconds: int,
    ) -> Sequence[ScheduledPrompt]:
        lease_deadline = now + timedelta(seconds=lease_timeout_seconds)
        async with self._session_factory() as session:
            stmt = (
                select(ScheduledPromptModel)
                .where(ScheduledPromptModel.run_at <= now)
                .where(
                    or_(
                        ScheduledPromptModel.status == ScheduledPromptStatus.PENDING.value,
                        and_(
                            ScheduledPromptModel.status == ScheduledPromptStatus.LEASED.value,
                            or_(
                                ScheduledPromptModel.lease_expires_at.is_(None),
                                ScheduledPromptModel.lease_expires_at <= now,
                            ),
                        ),
                    )
                )
                .order_by(ScheduledPromptModel.run_at)
                .limit(limit * 4)
            )
            result = await session.execute(stmt)
            candidates = list(result.scalars().all())
            leased: list[ScheduledPrompt] = []
            for record in candidates:
                if len(leased) >= limit:
                    break
                update_stmt = (
                    update(ScheduledPromptModel)
                    .where(ScheduledPromptModel.id == record.id)
                    .where(
                        or_(
                            ScheduledPromptModel.status == ScheduledPromptStatus.PENDING.value,
                            and_(
                                ScheduledPromptModel.status == ScheduledPromptStatus.LEASED.value,
                                or_(
                                    ScheduledPromptModel.lease_expires_at.is_(None),
                                    ScheduledPromptModel.lease_expires_at <= now,
                                ),
                            ),
                        )
                    )
                    .values(
                        status=ScheduledPromptStatus.LEASED.value,
                        lease_expires_at=lease_deadline,
                        updated_at=now,
                    )
                )
                result = await session.execute(update_stmt.execution_options(synchronize_session=False))
                rowcount = getattr(result, "rowcount", 0)
                if rowcount:
                    await session.refresh(record)
                    leased.append(self._to_domain(record))
            await session.commit()
            return leased

    async def mark_completed(self, job_id: str) -> None:
        await self._update_job(
            job_id,
            {
                "status": ScheduledPromptStatus.COMPLETED.value,
                "lease_expires_at": None,
                "last_error": None,
            },
        )

    async def retry_job(self, job_id: str, next_run_at: datetime, error: str | None = None) -> None:
        now = _utcnow()
        async with self._session_factory() as session:
            await session.execute(
                update(ScheduledPromptModel)
                .where(ScheduledPromptModel.id == job_id)
                .values(
                    status=ScheduledPromptStatus.PENDING.value,
                    run_at=_ensure_utc(next_run_at),
                    lease_expires_at=None,
                    last_error=error,
                    retry_count=ScheduledPromptModel.retry_count + 1,
                    updated_at=now,
                )
            )
            await session.commit()

    async def mark_failed(self, job_id: str, error: str | None = None) -> None:
        await self._update_job(
            job_id,
            {
                "status": ScheduledPromptStatus.FAILED.value,
                "lease_expires_at": None,
                "last_error": error,
            },
        )

    async def get(self, job_id: str) -> ScheduledPrompt | None:
        async with self._session_factory() as session:
            stmt = select(ScheduledPromptModel).where(ScheduledPromptModel.id == job_id).limit(1)
            result = await session.execute(stmt)
            model = result.scalars().first()
            return self._to_domain(model) if model else None

    async def _update_job(self, job_id: str, values: dict[str, Any]) -> None:
        now = _utcnow()
        payload = dict(values)
        payload.setdefault("updated_at", now)
        async with self._session_factory() as session:
            await session.execute(
                update(ScheduledPromptModel).where(ScheduledPromptModel.id == job_id).values(**payload)
            )
            await session.commit()

    def _to_domain(self, model: ScheduledPromptModel) -> ScheduledPrompt:
        run_at = _ensure_utc(model.run_at)
        lease_expires = _as_utc(model.lease_expires_at)
        return ScheduledPrompt(
            id=model.id,
            owner_id=model.owner_id,
            channel=model.channel,
            text=model.content,
            run_at=run_at,
            status=ScheduledPromptStatus(model.status),
            chat_id=model.chat_id,
            user_id=model.user_id,
            role=PromptRole(model.role),
            lease_expires_at=lease_expires,
            retry_count=model.retry_count,
            max_attempts=model.max_attempts,
            metadata=dict(model.metadata_payload or {}),
            last_error=model.last_error,
        )

    def _resolve_storage_path(self, sqlite_url: str) -> Path | None:
        url = make_url(sqlite_url)
        if url.database and url.drivername.startswith("sqlite") and url.database != ":memory":
            return Path(url.database)
        return None

    def _ensure_storage_dir(self, path: Path) -> None:
        directory = path.parent
        if directory and not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
