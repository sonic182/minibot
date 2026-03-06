from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence


class PromptRole(str, Enum):
    USER = "user"
    SYSTEM = "system"
    DEVELOPER = "developer"
    AGENT = "agent"


class ScheduledPromptStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PromptRecurrence(str, Enum):
    NONE = "none"
    INTERVAL = "interval"


class AgentJobStatus(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELED = "canceled"


@dataclass(slots=True)
class ScheduledPrompt:
    id: str
    owner_id: str
    channel: str
    text: str
    run_at: datetime
    status: ScheduledPromptStatus = ScheduledPromptStatus.PENDING
    chat_id: int | None = None
    user_id: int | None = None
    role: PromptRole = PromptRole.USER
    lease_expires_at: datetime | None = None
    retry_count: int = 0
    max_attempts: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)
    last_error: str | None = None
    recurrence: PromptRecurrence = PromptRecurrence.NONE
    recurrence_interval_seconds: int | None = None
    recurrence_end_at: datetime | None = None

    def should_retry(self) -> bool:
        return self.retry_count < self.max_attempts


@dataclass(slots=True)
class ScheduledPromptCreate:
    owner_id: str
    channel: str
    text: str
    run_at: datetime
    chat_id: int | None = None
    user_id: int | None = None
    role: PromptRole = PromptRole.USER
    metadata: Mapping[str, Any] | None = None
    max_attempts: int = 3
    recurrence: PromptRecurrence = PromptRecurrence.NONE
    recurrence_interval_seconds: int | None = None
    recurrence_end_at: datetime | None = None


@dataclass(slots=True)
class AgentJob:
    id: str
    agent_name: str
    status: AgentJobStatus
    created_at: datetime
    owner_id: str | None = None
    channel: str | None = None
    chat_id: int | None = None
    user_id: int | None = None
    created_by_session_id: str | None = None
    created_by_message_id: int | None = None
    correlation_id: str | None = None
    input_payload: dict[str, Any] = field(default_factory=dict)
    result_payload: dict[str, Any] | None = None
    error_payload: dict[str, Any] | None = None
    requested_mode: str = "async"
    attempt_count: int = 0
    max_attempts: int = 1
    timeout_seconds: int = 90
    lease_expires_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    worker_id: str | None = None
    process_pid: int | None = None
    cancel_requested_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status in {AgentJobStatus.QUEUED, AgentJobStatus.LEASED, AgentJobStatus.RUNNING}


@dataclass(slots=True)
class AgentJobCreate:
    agent_name: str
    input_payload: Mapping[str, Any]
    owner_id: str | None = None
    channel: str | None = None
    chat_id: int | None = None
    user_id: int | None = None
    created_by_session_id: str | None = None
    created_by_message_id: int | None = None
    correlation_id: str | None = None
    requested_mode: str = "async"
    max_attempts: int = 1
    timeout_seconds: int = 90


class ScheduledPromptRepository(Protocol):
    async def initialize(self) -> None: ...

    async def create(self, prompt: ScheduledPromptCreate) -> ScheduledPrompt: ...

    async def lease_due_jobs(
        self,
        *,
        now: datetime,
        limit: int,
        lease_timeout_seconds: int,
    ) -> Sequence[ScheduledPrompt]: ...

    async def mark_completed(self, job_id: str) -> None: ...

    async def retry_job(self, job_id: str, next_run_at: datetime, error: str | None = None) -> None: ...

    async def reschedule_recurring(self, job_id: str, next_run_at: datetime) -> None: ...

    async def mark_failed(self, job_id: str, error: str | None = None) -> None: ...

    async def mark_cancelled(self, job_id: str) -> None: ...

    async def delete_job(self, job_id: str) -> bool: ...

    async def get(self, job_id: str) -> ScheduledPrompt | None: ...

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
    ) -> Sequence[ScheduledPrompt]: ...


class AgentJobRepository(Protocol):
    async def initialize(self) -> None: ...

    async def create_job(self, job: AgentJobCreate) -> AgentJob: ...

    async def lease_next_jobs(
        self,
        *,
        now: datetime,
        limit: int,
        lease_timeout_seconds: int,
    ) -> Sequence[AgentJob]: ...

    async def mark_running(
        self,
        job_id: str,
        *,
        worker_id: str,
        process_pid: int | None,
        started_at: datetime,
        lease_expires_at: datetime | None,
    ) -> None: ...

    async def mark_completed(
        self,
        job_id: str,
        *,
        result_payload: Mapping[str, Any],
        finished_at: datetime,
    ) -> None: ...

    async def mark_failed(
        self,
        job_id: str,
        *,
        error_payload: Mapping[str, Any],
        finished_at: datetime,
    ) -> None: ...

    async def mark_timed_out(
        self,
        job_id: str,
        *,
        error_payload: Mapping[str, Any],
        finished_at: datetime,
    ) -> None: ...

    async def mark_canceled(
        self,
        job_id: str,
        *,
        error_payload: Mapping[str, Any] | None,
        finished_at: datetime,
    ) -> None: ...

    async def request_cancel(self, job_id: str, *, requested_at: datetime) -> bool: ...

    async def requeue_stale_jobs(self, *, now: datetime) -> Sequence[AgentJob]: ...

    async def get_job(self, job_id: str) -> AgentJob | None: ...

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
    ) -> Sequence[AgentJob]: ...
