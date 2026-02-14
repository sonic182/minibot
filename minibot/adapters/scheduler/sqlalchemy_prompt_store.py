from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Sequence
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text, and_, delete, or_, select, text, update
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from minibot.adapters.config.schema import ScheduledPromptsConfig
from minibot.adapters.sqlalchemy_utils import ensure_parent_dir, resolve_sqlite_storage_path
from minibot.core.jobs import (
    PromptRecurrence,
    PromptRole,
    ScheduledPrompt,
    ScheduledPromptCreate,
    ScheduledPromptRepository,
    ScheduledPromptStatus,
)
from minibot.shared.datetime_utils import ensure_utc, utcnow


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return ensure_utc(value)


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
    recurrence: Mapped[str] = mapped_column(String(16), nullable=False, default=PromptRecurrence.NONE.value)
    recurrence_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recurrence_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class SQLAlchemyScheduledPromptStore(ScheduledPromptRepository):
    def __init__(self, config: ScheduledPromptsConfig) -> None:
        self._config = config
        self._database_url = make_url(config.sqlite_url)
        storage_path = resolve_sqlite_storage_path(config.sqlite_url)
        if storage_path:
            ensure_parent_dir(storage_path)

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
            await connection.run_sync(self._initialize_schema)

    @staticmethod
    def _initialize_schema(connection: Connection) -> None:
        Base.metadata.create_all(connection)
        SQLAlchemyScheduledPromptStore._ensure_additive_schema(connection)

    @staticmethod
    def _ensure_additive_schema(connection: Connection) -> None:
        if connection.dialect.name != "sqlite":
            return
        columns = {str(row[1]) for row in connection.execute(text("PRAGMA table_info(scheduled_prompts)"))}
        if "recurrence" not in columns:
            connection.execute(
                text("ALTER TABLE scheduled_prompts ADD COLUMN recurrence VARCHAR(16) NOT NULL DEFAULT 'none'")
            )
        if "recurrence_interval_seconds" not in columns:
            connection.execute(text("ALTER TABLE scheduled_prompts ADD COLUMN recurrence_interval_seconds INTEGER"))
        if "recurrence_end_at" not in columns:
            connection.execute(text("ALTER TABLE scheduled_prompts ADD COLUMN recurrence_end_at DATETIME"))

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
                run_at=ensure_utc(prompt.run_at),
                retry_count=0,
                max_attempts=prompt.max_attempts,
                metadata_payload=metadata,
                recurrence=prompt.recurrence.value,
                recurrence_interval_seconds=prompt.recurrence_interval_seconds,
                recurrence_end_at=_as_utc(prompt.recurrence_end_at),
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
        now = utcnow()
        async with self._session_factory() as session:
            await session.execute(
                update(ScheduledPromptModel)
                .where(ScheduledPromptModel.id == job_id)
                .values(
                    status=ScheduledPromptStatus.PENDING.value,
                    run_at=ensure_utc(next_run_at),
                    lease_expires_at=None,
                    last_error=error,
                    retry_count=ScheduledPromptModel.retry_count + 1,
                    updated_at=now,
                )
            )
            await session.commit()

    async def reschedule_recurring(self, job_id: str, next_run_at: datetime) -> None:
        now = utcnow()
        async with self._session_factory() as session:
            await session.execute(
                update(ScheduledPromptModel)
                .where(ScheduledPromptModel.id == job_id)
                .values(
                    status=ScheduledPromptStatus.PENDING.value,
                    run_at=ensure_utc(next_run_at),
                    lease_expires_at=None,
                    last_error=None,
                    retry_count=0,
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

    async def mark_cancelled(self, job_id: str) -> None:
        await self._update_job(
            job_id,
            {
                "status": ScheduledPromptStatus.CANCELLED.value,
                "lease_expires_at": None,
                "last_error": None,
            },
        )

    async def delete_job(self, job_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(delete(ScheduledPromptModel).where(ScheduledPromptModel.id == job_id))
            await session.commit()
            return bool(getattr(result, "rowcount", 0))

    async def get(self, job_id: str) -> ScheduledPrompt | None:
        async with self._session_factory() as session:
            stmt = select(ScheduledPromptModel).where(ScheduledPromptModel.id == job_id).limit(1)
            result = await session.execute(stmt)
            model = result.scalars().first()
            return self._to_domain(model) if model else None

    async def list_jobs(
        self,
        *,
        owner_id: str,
        channel: str | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
        statuses: Sequence[ScheduledPromptStatus] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[ScheduledPrompt]:
        resolved_limit = max(1, min(limit, 100))
        resolved_offset = max(offset, 0)
        filters = [ScheduledPromptModel.owner_id == owner_id]
        if channel is not None:
            filters.append(ScheduledPromptModel.channel == channel)
        if chat_id is not None:
            filters.append(ScheduledPromptModel.chat_id == chat_id)
        if user_id is not None:
            filters.append(ScheduledPromptModel.user_id == user_id)
        if statuses:
            status_values = [status.value for status in statuses]
            filters.append(ScheduledPromptModel.status.in_(status_values))

        async with self._session_factory() as session:
            stmt = (
                select(ScheduledPromptModel)
                .where(*filters)
                .order_by(ScheduledPromptModel.run_at.asc(), ScheduledPromptModel.created_at.desc())
                .offset(resolved_offset)
                .limit(resolved_limit)
            )
            result = await session.execute(stmt)
            return [self._to_domain(model) for model in result.scalars().all()]

    async def _update_job(self, job_id: str, values: dict[str, Any]) -> None:
        now = utcnow()
        payload = dict(values)
        payload.setdefault("updated_at", now)
        async with self._session_factory() as session:
            await session.execute(
                update(ScheduledPromptModel).where(ScheduledPromptModel.id == job_id).values(**payload)
            )
            await session.commit()

    def _to_domain(self, model: ScheduledPromptModel) -> ScheduledPrompt:
        run_at = ensure_utc(model.run_at)
        lease_expires = _as_utc(model.lease_expires_at)
        recurrence = PromptRecurrence.NONE
        if model.recurrence:
            with_value = str(model.recurrence).lower()
            for candidate in PromptRecurrence:
                if candidate.value == with_value:
                    recurrence = candidate
                    break
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
            recurrence=recurrence,
            recurrence_interval_seconds=model.recurrence_interval_seconds,
            recurrence_end_at=_as_utc(model.recurrence_end_at),
        )
