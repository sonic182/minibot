from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class MemoryEntry:
    role: str
    content: str
    created_at: datetime
    reasoning: str | None = None
    id: int | None = None


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    message_count: int
    last_activity: datetime
    match_count: int | None = None


@dataclass(frozen=True)
class HistoryPage:
    """Messages newest first; pass ``next_before_id`` back as ``before_id`` for the older page."""

    entries: Sequence[MemoryEntry]
    next_before_id: int | None


@dataclass(frozen=True)
class SessionPage:
    """Sessions by latest activity; pass ``next_cursor`` back as ``cursor`` for the next page."""

    sessions: Sequence[SessionSummary]
    next_cursor: str | None


class MemoryBackend(Protocol):
    async def append_history(
        self, session_id: str, role: str, content: str, *, reasoning: str | None = None
    ) -> None: ...

    async def get_history(self, session_id: str, limit: int | None = None) -> Iterable[MemoryEntry]: ...

    async def count_history(self, session_id: str) -> int: ...

    async def trim_history(self, session_id: str, keep_latest: int) -> int: ...

    async def get_history_page(
        self, session_id: str, *, before_id: int | None = None, query: str | None = None, limit: int = 50
    ) -> HistoryPage: ...

    async def list_sessions(
        self, *, query: str | None = None, cursor: str | None = None, limit: int = 50
    ) -> SessionPage: ...


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


@dataclass(frozen=True)
class KeyValueCreateResult:
    entry: KeyValueEntry
    created: bool


@dataclass(frozen=True)
class KeyValueMemoryFilter:
    category: str | None = None
    source: str | None = None
    updated_after: datetime | None = None
    updated_before: datetime | None = None


class KeyValueMemory(Protocol):
    async def create_entry(
        self,
        owner_id: str,
        title: str,
        data: str,
        metadata: Mapping[str, Any] | None = None,
        source: str | None = None,
        expires_at: datetime | None = None,
    ) -> KeyValueCreateResult: ...

    async def update_entry(
        self,
        owner_id: str,
        entry_id: str,
        data: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        source: str | None = None,
        expires_at: datetime | None = None,
    ) -> KeyValueEntry | None: ...

    async def get_entry(
        self,
        owner_id: str,
        entry_id: str,
    ) -> KeyValueEntry | None: ...

    async def delete_entry(
        self,
        owner_id: str,
        entry_id: str,
    ) -> bool: ...

    async def search_entries(
        self,
        owner_id: str,
        query: str | None = None,
        filters: KeyValueMemoryFilter | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> KeyValueSearchResult: ...

    async def list_entries(
        self,
        owner_id: str,
        filters: KeyValueMemoryFilter | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> KeyValueSearchResult: ...
