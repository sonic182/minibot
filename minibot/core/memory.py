from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Protocol, Sequence


@dataclass(frozen=True)
class MemoryEntry:
    role: str
    content: str
    created_at: datetime


class MemoryBackend(Protocol):
    async def append_history(self, session_id: str, role: str, content: str) -> None: ...

    async def get_history(self, session_id: str, limit: int = 32) -> Iterable[MemoryEntry]: ...

    async def count_history(self, session_id: str) -> int: ...

    async def trim_history(self, session_id: str, keep_latest: int) -> int: ...


@dataclass(frozen=True)
class KeyValueEntry:
    id: str
    owner_id: str
    title: str
    data: str
    metadata: Mapping[str, Any]
    source: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True)
class KeyValueSearchResult:
    entries: Sequence[KeyValueEntry]
    total: int
    limit: int
    offset: int


class KeyValueMemory(Protocol):
    async def save_entry(
        self,
        owner_id: str,
        title: str,
        data: str,
        metadata: Mapping[str, Any] | None = None,
        source: str | None = None,
        expires_at: datetime | None = None,
    ) -> KeyValueEntry: ...

    async def get_entry(
        self,
        owner_id: str,
        entry_id: str | None = None,
        title: str | None = None,
    ) -> KeyValueEntry | None: ...

    async def search_entries(
        self,
        owner_id: str,
        query: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> KeyValueSearchResult: ...

    async def list_entries(
        self,
        owner_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> KeyValueSearchResult: ...
