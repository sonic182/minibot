from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class TaskStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"


@dataclass(slots=True)
class TaskRequest:
    task_id: str
    channel: str
    prompt: str
    agent_name: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    chat_id: int | None = None
    user_id: int | None = None


@dataclass(slots=True)
class TaskRecord:
    """A stored queue row: the original request plus the queue's own bookkeeping."""

    request: TaskRequest
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    max_attempts: int = 3
    last_error: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskProducer(Protocol):
    async def enqueue(self, task: TaskRequest) -> None: ...
