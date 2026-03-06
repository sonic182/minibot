from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, or_, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from minibot.adapters.config.schema import JobsConfig
from minibot.adapters.sqlalchemy_utils import ensure_parent_dir, resolve_sqlite_storage_path
from minibot.core.jobs import AgentJob, AgentJobCreate, AgentJobRepository, AgentJobStatus
from minibot.shared.datetime_utils import ensure_utc, utcnow


Base = declarative_base()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return ensure_utc(value)


class AgentJobModel(Base):
    __tablename__ = "agent_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    channel: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    created_by_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_by_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    requested_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="async")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    process_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class SQLAlchemyAgentJobStore(AgentJobRepository):
    def __init__(self, config: JobsConfig) -> None:
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
            await connection.run_sync(Base.metadata.create_all)

    async def create_job(self, job: AgentJobCreate) -> AgentJob:
        async with self._session_factory() as session:
            model = AgentJobModel(
                id=uuid4().hex,
                agent_name=job.agent_name,
                status=AgentJobStatus.QUEUED.value,
                owner_id=job.owner_id,
                channel=job.channel,
                chat_id=job.chat_id,
                user_id=job.user_id,
                created_by_session_id=job.created_by_session_id,
                created_by_message_id=job.created_by_message_id,
                correlation_id=job.correlation_id,
                input_payload=dict(job.input_payload),
                requested_mode=job.requested_mode,
                max_attempts=job.max_attempts,
                timeout_seconds=job.timeout_seconds,
            )
            session.add(model)
            await session.commit()
            await session.refresh(model)
            return self._to_domain(model)

    async def lease_next_jobs(
        self,
        *,
        now: datetime,
        limit: int,
        lease_timeout_seconds: int,
    ) -> Sequence[AgentJob]:
        lease_deadline = ensure_utc(now) + timedelta(seconds=lease_timeout_seconds)
        async with self._session_factory() as session:
            stmt = (
                select(AgentJobModel)
                .where(
                    AgentJobModel.status == AgentJobStatus.QUEUED.value,
                )
                .where(AgentJobModel.cancel_requested_at.is_(None))
                .order_by(AgentJobModel.created_at.asc())
                .limit(limit * 4)
            )
            result = await session.execute(stmt)
            candidates = list(result.scalars().all())
            leased: list[AgentJob] = []
            for record in candidates:
                if len(leased) >= limit:
                    break
                update_stmt = (
                    update(AgentJobModel)
                    .where(AgentJobModel.id == record.id)
                    .where(AgentJobModel.status == AgentJobStatus.QUEUED.value)
                    .where(AgentJobModel.cancel_requested_at.is_(None))
                    .values(
                        status=AgentJobStatus.LEASED.value,
                        lease_expires_at=lease_deadline,
                        attempt_count=AgentJobModel.attempt_count + 1,
                        updated_at=ensure_utc(now),
                    )
                )
                lease_result = await session.execute(update_stmt.execution_options(synchronize_session=False))
                if getattr(lease_result, "rowcount", 0):
                    await session.refresh(record)
                    leased.append(self._to_domain(record))
            await session.commit()
            return leased

    async def mark_running(
        self,
        job_id: str,
        *,
        worker_id: str,
        process_pid: int | None,
        started_at: datetime,
        lease_expires_at: datetime | None,
    ) -> None:
        await self._update_job(
            job_id,
            {
                "status": AgentJobStatus.RUNNING.value,
                "worker_id": worker_id,
                "process_pid": process_pid,
                "started_at": ensure_utc(started_at),
                "lease_expires_at": _as_utc(lease_expires_at),
            },
        )

    async def mark_completed(self, job_id: str, *, result_payload: Mapping[str, Any], finished_at: datetime) -> None:
        await self._update_job(
            job_id,
            {
                "status": AgentJobStatus.COMPLETED.value,
                "result_payload": dict(result_payload),
                "error_payload": None,
                "finished_at": ensure_utc(finished_at),
                "lease_expires_at": None,
            },
        )

    async def mark_failed(
        self,
        job_id: str,
        *,
        error_payload: Mapping[str, Any],
        finished_at: datetime,
    ) -> None:
        await self._update_job(
            job_id,
            {
                "status": AgentJobStatus.FAILED.value,
                "error_payload": dict(error_payload),
                "finished_at": ensure_utc(finished_at),
                "lease_expires_at": None,
            },
        )

    async def mark_timed_out(
        self,
        job_id: str,
        *,
        error_payload: Mapping[str, Any],
        finished_at: datetime,
    ) -> None:
        await self._update_job(
            job_id,
            {
                "status": AgentJobStatus.TIMED_OUT.value,
                "error_payload": dict(error_payload),
                "finished_at": ensure_utc(finished_at),
                "lease_expires_at": None,
            },
        )

    async def mark_canceled(
        self,
        job_id: str,
        *,
        error_payload: Mapping[str, Any] | None,
        finished_at: datetime,
    ) -> None:
        values: dict[str, Any] = {
            "status": AgentJobStatus.CANCELED.value,
            "finished_at": ensure_utc(finished_at),
            "lease_expires_at": None,
        }
        values["error_payload"] = dict(error_payload) if error_payload is not None else None
        await self._update_job(job_id, values)

    async def request_cancel(self, job_id: str, *, requested_at: datetime) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentJobModel)
                .where(AgentJobModel.id == job_id)
                .where(
                    AgentJobModel.status.in_(
                        [
                            AgentJobStatus.QUEUED.value,
                            AgentJobStatus.LEASED.value,
                            AgentJobStatus.RUNNING.value,
                        ]
                    )
                )
                .values(cancel_requested_at=ensure_utc(requested_at), updated_at=ensure_utc(requested_at))
            )
            await session.commit()
            return bool(getattr(result, "rowcount", 0))

    async def requeue_stale_jobs(self, *, now: datetime) -> Sequence[AgentJob]:
        now_utc = ensure_utc(now)
        async with self._session_factory() as session:
            stmt = (
                select(AgentJobModel)
                .where(AgentJobModel.status.in_([AgentJobStatus.LEASED.value, AgentJobStatus.RUNNING.value]))
                .where(
                    or_(
                        AgentJobModel.lease_expires_at.is_(None),
                        AgentJobModel.lease_expires_at <= now_utc,
                    )
                )
            )
            result = await session.execute(stmt)
            stale = list(result.scalars().all())
            requeued: list[AgentJob] = []
            for record in stale:
                can_retry = record.attempt_count < max(record.max_attempts, 1)
                status = AgentJobStatus.QUEUED.value if can_retry else AgentJobStatus.FAILED.value
                error_payload = None
                if not can_retry:
                    error_payload = {
                        "error_code": "stale_job_recovery_exhausted",
                        "error": "job lease expired",
                    }
                await session.execute(
                    update(AgentJobModel)
                    .where(AgentJobModel.id == record.id)
                    .values(
                        status=status,
                        worker_id=None,
                        process_pid=None,
                        started_at=None if can_retry else record.started_at,
                        lease_expires_at=None,
                        error_payload=error_payload,
                        updated_at=now_utc,
                    )
                )
                await session.refresh(record)
                requeued.append(self._to_domain(record))
            await session.commit()
            return requeued

    async def get_job(self, job_id: str) -> AgentJob | None:
        async with self._session_factory() as session:
            stmt = select(AgentJobModel).where(AgentJobModel.id == job_id).limit(1)
            result = await session.execute(stmt)
            model = result.scalars().first()
            return self._to_domain(model) if model is not None else None

    async def list_jobs(
        self,
        *,
        owner_id: str | None = None,
        channel: str | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
        statuses: Sequence[AgentJobStatus] | None = None,
        cancel_requested_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> Sequence[AgentJob]:
        filters = []
        if owner_id is not None:
            filters.append(AgentJobModel.owner_id == owner_id)
        if channel is not None:
            filters.append(AgentJobModel.channel == channel)
        if chat_id is not None:
            filters.append(AgentJobModel.chat_id == chat_id)
        if user_id is not None:
            filters.append(AgentJobModel.user_id == user_id)
        if statuses:
            filters.append(AgentJobModel.status.in_([status.value for status in statuses]))
        if cancel_requested_only:
            filters.append(AgentJobModel.cancel_requested_at.is_not(None))
        async with self._session_factory() as session:
            stmt = (
                select(AgentJobModel)
                .where(*filters)
                .order_by(AgentJobModel.created_at.desc())
                .offset(max(offset, 0))
                .limit(max(1, min(limit, 100)))
            )
            result = await session.execute(stmt)
            return [self._to_domain(model) for model in result.scalars().all()]

    async def _update_job(self, job_id: str, values: Mapping[str, Any]) -> None:
        payload = dict(values)
        payload.setdefault("updated_at", utcnow())
        async with self._session_factory() as session:
            await session.execute(update(AgentJobModel).where(AgentJobModel.id == job_id).values(**payload))
            await session.commit()

    @staticmethod
    def _to_domain(model: AgentJobModel) -> AgentJob:
        return AgentJob(
            id=model.id,
            agent_name=model.agent_name,
            status=AgentJobStatus(model.status),
            created_at=ensure_utc(model.created_at),
            owner_id=model.owner_id,
            channel=model.channel,
            chat_id=model.chat_id,
            user_id=model.user_id,
            created_by_session_id=model.created_by_session_id,
            created_by_message_id=model.created_by_message_id,
            correlation_id=model.correlation_id,
            input_payload=dict(model.input_payload or {}),
            result_payload=dict(model.result_payload) if model.result_payload is not None else None,
            error_payload=dict(model.error_payload) if model.error_payload is not None else None,
            requested_mode=model.requested_mode,
            attempt_count=model.attempt_count,
            max_attempts=model.max_attempts,
            timeout_seconds=model.timeout_seconds,
            lease_expires_at=_as_utc(model.lease_expires_at),
            started_at=_as_utc(model.started_at),
            finished_at=_as_utc(model.finished_at),
            worker_id=model.worker_id,
            process_pid=model.process_pid,
            cancel_requested_at=_as_utc(model.cancel_requested_at),
        )
