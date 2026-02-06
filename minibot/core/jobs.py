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

    async def mark_failed(self, job_id: str, error: str | None = None) -> None: ...
