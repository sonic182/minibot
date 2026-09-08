from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text, delete, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from minibot.adapters.config.schema import SqliteTaskQueueConfig
from minibot.adapters.sqlalchemy_utils import ensure_parent_dir, lease_rows, resolve_sqlite_storage_path
from minibot.core.tasks import TaskRecord, TaskRequest, TaskStatus
from minibot.shared.datetime_utils import ensure_utc, utcnow

# Each store module owns its declarative base so create_all only touches its own table.
TaskBase = declarative_base()


class TaskModel(TaskBase):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class SQLiteTaskStore:
    """Durable task queue backing ``tasks.backend = "sqlite"``.

    Tasks are claimed FIFO by ``created_at``; every row is always due, so unlike the scheduler store
    there is no ``run_at`` filter.
    """

    def __init__(self, config: SqliteTaskQueueConfig) -> None:
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
            await connection.run_sync(TaskBase.metadata.create_all)

    async def create(self, task: TaskRequest) -> None:
        async with self._session_factory() as session:
            session.add(
                TaskModel(
                    id=task.task_id,
                    channel=task.channel,
                    chat_id=task.chat_id,
                    user_id=task.user_id,
                    prompt=task.prompt,
                    agent_name=task.agent_name,
                    context=dict(task.context or {}),
                    status=TaskStatus.PENDING.value,
                    retry_count=0,
                    max_attempts=self._config.max_attempts,
                )
            )
            await session.commit()

    async def lease_due_tasks(
        self,
        *,
        now: datetime,
        limit: int,
        lease_timeout_seconds: int,
    ) -> Sequence[TaskRecord]:
        async with self._session_factory() as session:
            records = await lease_rows(
                session,
                TaskModel,
                now=now,
                limit=limit,
                lease_deadline=now + timedelta(seconds=lease_timeout_seconds),
                order_by=TaskModel.created_at,
            )
            leased = [_to_domain(record) for record in records]
            await session.commit()
            return leased

    async def mark_done(self, task_id: str) -> None:
        await self._update(
            task_id,
            {"status": TaskStatus.DONE.value, "lease_expires_at": None, "last_error": None},
        )

    async def mark_failed(self, task_id: str, error: str | None = None) -> None:
        await self._update(
            task_id,
            {"status": TaskStatus.FAILED.value, "lease_expires_at": None, "last_error": error},
        )

    async def retry_task(self, task_id: str, error: str | None = None) -> TaskStatus | None:
        """Return the task to the queue, or fail it permanently once ``max_attempts`` is reached."""
        async with self._session_factory() as session:
            result = await session.execute(select(TaskModel).where(TaskModel.id == task_id).limit(1))
            record = result.scalars().first()
            if record is None:
                return None
            retry_count = record.retry_count + 1
            status = TaskStatus.FAILED if retry_count >= record.max_attempts else TaskStatus.PENDING
            record.status = status.value
            record.retry_count = retry_count
            record.lease_expires_at = None
            record.last_error = error
            record.updated_at = utcnow()
            await session.commit()
            return status

    async def purge_done(self, before: datetime) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(TaskModel)
                .where(TaskModel.status == TaskStatus.DONE.value)
                .where(TaskModel.updated_at < ensure_utc(before))
            )
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    async def get(self, task_id: str) -> TaskRecord | None:
        async with self._session_factory() as session:
            result = await session.execute(select(TaskModel).where(TaskModel.id == task_id).limit(1))
            record = result.scalars().first()
            return _to_domain(record) if record else None

    async def _update(self, task_id: str, values: dict[str, Any]) -> None:
        payload = dict(values)
        payload.setdefault("updated_at", utcnow())
        async with self._session_factory() as session:
            await session.execute(update(TaskModel).where(TaskModel.id == task_id).values(**payload))
            await session.commit()


class SQLiteTaskProducer:
    """``TaskProducer`` backed by :class:`SQLiteTaskStore`; enqueueing is just inserting a row."""

    def __init__(self, store: SQLiteTaskStore) -> None:
        self._store = store

    async def enqueue(self, task: TaskRequest) -> None:
        await self._store.create(task)


def _to_domain(model: TaskModel) -> TaskRecord:
    return TaskRecord(
        request=TaskRequest(
            task_id=model.id,
            channel=model.channel,
            prompt=model.prompt,
            agent_name=model.agent_name,
            context=dict(model.context or {}),
            chat_id=model.chat_id,
            user_id=model.user_id,
        ),
        status=TaskStatus(model.status),
        retry_count=model.retry_count,
        max_attempts=model.max_attempts,
        last_error=model.last_error,
        lease_expires_at=_as_utc(model.lease_expires_at),
        created_at=_as_utc(model.created_at),
        updated_at=_as_utc(model.updated_at),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    return None if value is None else ensure_utc(value)
