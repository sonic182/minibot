from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class TaskStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    RETRYING = "retrying"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class TaskStopReason(StrEnum):
    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    MAX_TOOL_CALLS = "max_tool_calls"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PROVIDER_ERROR = "provider_error"
    REPEATED_TOOL_FAILURE = "repeated_tool_failure"
    REPEATED_ITERATION = "repeated_iteration"
    TRUNCATED_TOOL_CALL = "truncated_tool_call"
    INVALID_RESULT = "invalid_result"
    WORKER_ERROR = "worker_error"


@dataclass(frozen=True, slots=True)
class TaskLimits:
    timeout_seconds: int
    max_steps: int | None = None
    max_tool_calls: int | None = None


@dataclass(slots=True)
class TaskRequest:
    task_id: str
    channel: str
    prompt: str
    agent_name: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    chat_id: int | None = None
    user_id: int | None = None
    owner_id: str = "primary"
    limits: TaskLimits = field(default_factory=lambda: TaskLimits(timeout_seconds=1800))


@dataclass(slots=True)
class TaskResult:
    text: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    stop_reason: TaskStopReason = TaskStopReason.COMPLETED


@dataclass(slots=True)
class TaskRecord:
    """A stored queue row: the original request plus the queue's own bookkeeping."""

    request: TaskRequest
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    max_attempts: int = 3
    last_error: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    stop_reason: TaskStopReason | None = None
    result: TaskResult | None = None
    progress: dict[str, Any] = field(default_factory=dict)


class TaskProducer(Protocol):
    async def enqueue(self, task: TaskRequest) -> None: ...


class TaskRepository(Protocol):
    async def create(self, task: TaskRequest) -> None: ...

    async def get(self, task_id: str, owner_id: str | None = None) -> TaskRecord | None: ...

    async def list(self, *, owner_id: str, statuses: list[TaskStatus] | None, limit: int) -> list[TaskRecord]: ...

    async def claim_execution(
        self,
        task_id: str,
        *,
        expected_status: TaskStatus,
        lease_token: str | None,
        lease_timeout_seconds: int,
        replace_lease: bool = False,
    ) -> str | None: ...

    async def renew_execution(self, task_id: str, lease_token: str, lease_timeout_seconds: int) -> bool: ...

    async def update_progress(self, task_id: str, lease_token: str, progress: dict[str, Any]) -> bool: ...

    async def append_event(self, task_id: str, event_type: str, payload: dict[str, Any]) -> None: ...

    async def mark_done(self, task_id: str, result: TaskResult, lease_token: str | None = None) -> bool: ...

    async def mark_failed(
        self,
        task_id: str,
        error: str | None = None,
        stop_reason: TaskStopReason = TaskStopReason.WORKER_ERROR,
        status: TaskStatus = TaskStatus.FAILED,
        metadata: dict[str, Any] | None = None,
        lease_token: str | None = None,
    ) -> bool: ...

    async def mark_cancelled(self, task_id: str) -> bool: ...

    async def events(self, task_id: str, *, limit: int = 100) -> list[dict[str, Any]]: ...
