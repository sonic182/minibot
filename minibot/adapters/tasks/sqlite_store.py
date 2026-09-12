from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text, and_, delete, or_, select, text, update
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from minibot.adapters.config.schema import SqliteTaskQueueConfig
from minibot.adapters.sqlalchemy_utils import ensure_parent_dir, resolve_sqlite_storage_path
from minibot.core.tasks import TaskLimits, TaskRecord, TaskRequest, TaskResult, TaskStatus, TaskStopReason
from minibot.shared.datetime_utils import ensure_utc, utcnow

TaskBase = declarative_base()
_MAX_EVENTS_PER_TASK = 100


class TaskModel(TaskBase):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, default="primary", index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    max_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tool_calls: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    result_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class TaskEventModel(TaskBase):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)


class SQLiteTaskStore:
    """Durable queue and result history for task workers."""

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
            await connection.run_sync(self._initialize_schema)

    @staticmethod
    def _initialize_schema(connection: Connection) -> None:
        TaskBase.metadata.create_all(connection)
        if connection.dialect.name != "sqlite":
            return
        columns = {str(row[1]) for row in connection.execute(text("PRAGMA table_info(tasks)"))}
        additions = {
            "owner_id": "VARCHAR(128) NOT NULL DEFAULT 'primary'",
            "lease_token": "VARCHAR(36)",
            "timeout_seconds": "INTEGER NOT NULL DEFAULT 1800",
            "max_steps": "INTEGER",
            "max_tool_calls": "INTEGER",
            "progress": "JSON NOT NULL DEFAULT '{}'",
            "result_text": "TEXT",
            "result_attachments": "JSON NOT NULL DEFAULT '[]'",
            "result_metadata": "JSON NOT NULL DEFAULT '{}'",
            "stop_reason": "VARCHAR(32)",
            "started_at": "DATETIME",
            "completed_at": "DATETIME",
        }
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE tasks ADD COLUMN {name} {definition}"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_owner_id ON tasks (owner_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_lease_token ON tasks (lease_token)"))

    async def create(self, task: TaskRequest) -> None:
        async with self._session_factory() as session:
            if await session.get(TaskModel, task.task_id) is None:
                session.add(self._model_from_request(task))
                session.add(TaskEventModel(task_id=task.task_id, event_type="queued", payload={}))
            await session.commit()

    async def lease_due_tasks(
        self,
        *,
        now: datetime,
        limit: int,
        lease_timeout_seconds: int,
    ) -> Sequence[TaskRecord]:
        async with self._session_factory() as session:
            claimable = or_(
                TaskModel.status == TaskStatus.PENDING.value,
                and_(
                    TaskModel.status.in_([TaskStatus.LEASED.value, TaskStatus.RUNNING.value]),
                    or_(TaskModel.lease_expires_at.is_(None), TaskModel.lease_expires_at <= now),
                ),
            )
            candidates = list(
                (
                    await session.execute(
                        select(TaskModel).where(claimable).order_by(TaskModel.created_at).limit(limit * 4)
                    )
                )
                .scalars()
                .all()
            )
            records: list[TaskModel] = []
            for candidate in candidates:
                if len(records) >= limit:
                    break
                lease_token = uuid4().hex
                outcome = await session.execute(
                    update(TaskModel)
                    .where(TaskModel.id == candidate.id)
                    .where(claimable)
                    .values(
                        status=TaskStatus.LEASED.value,
                        lease_token=lease_token,
                        lease_expires_at=now + timedelta(seconds=lease_timeout_seconds),
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                if getattr(outcome, "rowcount", 0):
                    await session.refresh(candidate)
                    records.append(candidate)
            for record in records:
                session.add(TaskEventModel(task_id=record.id, event_type="leased", payload={}))
            leased = [_to_domain(record) for record in records]
            await session.commit()
            return leased

    async def claim_execution(
        self,
        task_id: str,
        *,
        expected_status: TaskStatus,
        lease_token: str | None,
        lease_timeout_seconds: int,
        replace_lease: bool = False,
    ) -> str | None:
        now = utcnow()
        active_token = uuid4().hex if replace_lease or lease_token is None else lease_token
        statement = update(TaskModel).where(TaskModel.id == task_id).where(TaskModel.status == expected_status.value)
        if lease_token is not None:
            statement = statement.where(TaskModel.lease_token == lease_token)
        async with self._session_factory() as session:
            outcome = await session.execute(
                statement.values(
                    status=TaskStatus.RUNNING.value,
                    lease_token=active_token,
                    lease_expires_at=now + timedelta(seconds=lease_timeout_seconds),
                    started_at=now,
                    progress={"phase": "running"},
                    updated_at=now,
                )
            )
            if not getattr(outcome, "rowcount", 0):
                await session.rollback()
                return None
            session.add(TaskEventModel(task_id=task_id, event_type="running", payload={}))
            await session.commit()
        return active_token

    async def renew_execution(self, task_id: str, lease_token: str, lease_timeout_seconds: int) -> bool:
        return await self._update_execution(
            task_id,
            lease_token,
            {"lease_expires_at": utcnow() + timedelta(seconds=lease_timeout_seconds)},
        )

    async def update_progress(self, task_id: str, lease_token: str, progress: dict[str, Any]) -> bool:
        updated = await self._update_execution(
            task_id,
            lease_token,
            {"progress": dict(progress)},
        )
        if updated:
            await self.append_event(task_id, "progress", progress)
        return updated

    async def append_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
        async with self._session_factory() as session:
            session.add(TaskEventModel(task_id=task_id, event_type=event_type, payload=dict(payload)))
            await session.flush()
            stale_event_ids = list(
                (
                    await session.execute(
                        select(TaskEventModel.id)
                        .where(TaskEventModel.task_id == task_id)
                        .order_by(TaskEventModel.id.desc())
                        .offset(_MAX_EVENTS_PER_TASK)
                    )
                ).scalars()
            )
            if stale_event_ids:
                await session.execute(delete(TaskEventModel).where(TaskEventModel.id.in_(stale_event_ids)))
            await session.commit()

    async def mark_done(self, task_id: str, result: TaskResult | None = None, lease_token: str | None = None) -> bool:
        task_result = result or TaskResult()
        updated = await self._update(
            task_id,
            {
                "status": TaskStatus.DONE.value,
                "lease_token": None,
                "lease_expires_at": None,
                "last_error": None,
                "stop_reason": task_result.stop_reason.value,
                "result_text": task_result.text,
                "result_attachments": list(task_result.attachments),
                "result_metadata": dict(task_result.metadata),
                "completed_at": utcnow(),
            },
            lease_token=lease_token,
        )
        if updated:
            await self.append_event(task_id, "done", {"stop_reason": task_result.stop_reason.value})
        return updated

    async def mark_failed(
        self,
        task_id: str,
        error: str | None = None,
        stop_reason: TaskStopReason = TaskStopReason.WORKER_ERROR,
        status: TaskStatus = TaskStatus.FAILED,
        metadata: dict[str, Any] | None = None,
        lease_token: str | None = None,
    ) -> bool:
        updated = await self._update(
            task_id,
            {
                "status": status.value,
                "lease_token": None,
                "lease_expires_at": None,
                "last_error": error,
                "stop_reason": stop_reason.value,
                "progress": {"phase": status.value},
                "result_metadata": dict(metadata or {}),
                "completed_at": utcnow(),
            },
            lease_token=lease_token,
        )
        if updated:
            await self.append_event(task_id, status.value, {"stop_reason": stop_reason.value})
        return updated

    async def mark_cancelled(self, task_id: str) -> bool:
        async with self._session_factory() as session:
            record = await session.get(TaskModel, task_id)
            if record is None or record.status in {
                TaskStatus.DONE.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
                TaskStatus.TIMED_OUT.value,
            }:
                return False
            record.status = TaskStatus.CANCELLED.value
            record.lease_token = None
            record.lease_expires_at = None
            record.stop_reason = TaskStopReason.CANCELLED.value
            record.completed_at = utcnow()
            record.updated_at = utcnow()
            session.add(TaskEventModel(task_id=task_id, event_type="cancelled", payload={}))
            await session.commit()
            return True

    async def retry_task(self, task_id: str, error: str | None = None) -> TaskStatus | None:
        async with self._session_factory() as session:
            record = await session.get(TaskModel, task_id)
            if record is None:
                return None
            retry_count = record.retry_count + 1
            status = TaskStatus.FAILED if retry_count >= record.max_attempts else TaskStatus.PENDING
            record.status = status.value
            record.lease_token = None
            record.retry_count = retry_count
            record.lease_expires_at = None
            record.last_error = error
            record.updated_at = utcnow()
            session.add(TaskEventModel(task_id=task_id, event_type=status.value, payload={"retry_count": retry_count}))
            await session.commit()
            return status

    async def purge_done(self, before: datetime) -> int:
        terminal_statuses = [
            TaskStatus.DONE.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.TIMED_OUT.value,
        ]
        async with self._session_factory() as session:
            identifiers = list(
                (
                    await session.execute(
                        select(TaskModel.id)
                        .where(TaskModel.status.in_(terminal_statuses))
                        .where(TaskModel.updated_at < ensure_utc(before))
                    )
                ).scalars()
            )
            if not identifiers:
                return 0
            await session.execute(delete(TaskEventModel).where(TaskEventModel.task_id.in_(identifiers)))
            result = await session.execute(delete(TaskModel).where(TaskModel.id.in_(identifiers)))
            await session.commit()
            return int(getattr(result, "rowcount", 0) or 0)

    async def get(self, task_id: str, owner_id: str | None = None) -> TaskRecord | None:
        async with self._session_factory() as session:
            statement = select(TaskModel).where(TaskModel.id == task_id).limit(1)
            if owner_id is not None:
                statement = statement.where(TaskModel.owner_id == owner_id)
            record = (await session.execute(statement)).scalars().first()
            return _to_domain(record) if record else None

    async def list(self, *, owner_id: str, statuses: list[TaskStatus] | None, limit: int) -> list[TaskRecord]:
        async with self._session_factory() as session:
            statement = (
                select(TaskModel)
                .where(TaskModel.owner_id == owner_id)
                .order_by(TaskModel.updated_at.desc())
                .limit(limit)
            )
            if statuses:
                statement = statement.where(TaskModel.status.in_([status.value for status in statuses]))
            records = (await session.execute(statement)).scalars().all()
            return [_to_domain(record) for record in records]

    async def events(self, task_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(TaskEventModel)
                        .where(TaskEventModel.task_id == task_id)
                        .order_by(TaskEventModel.id.asc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "event_type": row.event_type,
                    "payload": dict(row.payload or {}),
                    "created_at": _as_utc(row.created_at).isoformat(),
                }
                for row in rows
            ]

    async def _update(self, task_id: str, values: dict[str, Any], lease_token: str | None = None) -> bool:
        payload = dict(values)
        payload.setdefault("updated_at", utcnow())
        async with self._session_factory() as session:
            statement = update(TaskModel).where(TaskModel.id == task_id)
            if lease_token is not None:
                statement = statement.where(TaskModel.lease_token == lease_token).where(
                    TaskModel.status == TaskStatus.RUNNING.value
                )
            result = await session.execute(statement.values(**payload))
            await session.commit()
            return bool(getattr(result, "rowcount", 0))

    async def _update_execution(self, task_id: str, lease_token: str, values: dict[str, Any]) -> bool:
        return await self._update(task_id, values, lease_token=lease_token)

    def _model_from_request(self, task: TaskRequest) -> TaskModel:
        return TaskModel(
            id=task.task_id,
            owner_id=task.owner_id,
            channel=task.channel,
            chat_id=task.chat_id,
            user_id=task.user_id,
            prompt=task.prompt,
            agent_name=task.agent_name,
            context=dict(task.context or {}),
            status=TaskStatus.PENDING.value,
            retry_count=0,
            max_attempts=self._config.max_attempts,
            timeout_seconds=task.limits.timeout_seconds,
            max_steps=task.limits.max_steps,
            max_tool_calls=task.limits.max_tool_calls,
        )


class SQLiteTaskProducer:
    def __init__(self, store: SQLiteTaskStore) -> None:
        self._store = store

    async def enqueue(self, task: TaskRequest) -> None:
        await self._store.create(task)


def _to_domain(model: TaskModel) -> TaskRecord:
    result = None
    if model.result_text is not None or model.result_attachments or model.result_metadata:
        result = TaskResult(
            text=model.result_text or "",
            attachments=list(model.result_attachments or []),
            metadata=dict(model.result_metadata or {}),
            stop_reason=TaskStopReason(model.stop_reason or TaskStopReason.COMPLETED.value),
        )
    return TaskRecord(
        request=TaskRequest(
            task_id=model.id,
            channel=model.channel,
            prompt=model.prompt,
            agent_name=model.agent_name,
            context=dict(model.context or {}),
            chat_id=model.chat_id,
            user_id=model.user_id,
            owner_id=model.owner_id,
            limits=TaskLimits(
                timeout_seconds=model.timeout_seconds,
                max_steps=model.max_steps,
                max_tool_calls=model.max_tool_calls,
            ),
        ),
        status=TaskStatus(model.status),
        retry_count=model.retry_count,
        max_attempts=model.max_attempts,
        last_error=model.last_error,
        lease_token=model.lease_token,
        lease_expires_at=_as_utc(model.lease_expires_at),
        created_at=_as_utc(model.created_at),
        updated_at=_as_utc(model.updated_at),
        started_at=_as_utc(model.started_at),
        completed_at=_as_utc(model.completed_at),
        stop_reason=TaskStopReason(model.stop_reason) if model.stop_reason else None,
        result=result,
        progress=dict(model.progress or {}),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    return None if value is None else ensure_utc(value)
