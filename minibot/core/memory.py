from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol


@dataclass(frozen=True)
class MemoryEntry:
    role: str
    content: str
    created_at: datetime


class MemoryBackend(Protocol):
    async def append_history(self, session_id: str, role: str, content: str) -> None:
        ...

    async def get_history(self, session_id: str, limit: int = 32) -> Iterable[MemoryEntry]:
        ...
