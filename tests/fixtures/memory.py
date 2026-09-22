from __future__ import annotations

from datetime import UTC, datetime

from minibot.core.memory import HistoryPage, MemoryEntry, SessionPage, SessionSummary


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._store: dict[str, list[MemoryEntry]] = {}
        self._next_id = 1
        self.trim_calls: list[tuple[str, int]] = []

    async def append_history(self, session_id: str, role: str, content: str, *, reasoning: str | None = None) -> None:
        entry = MemoryEntry(
            role=role, content=content, created_at=datetime.now(UTC), reasoning=reasoning, id=self._next_id
        )
        self._next_id += 1
        self._store.setdefault(session_id, []).append(entry)

    async def get_history(self, session_id: str, limit: int | None = None) -> list[MemoryEntry]:
        entries = self._store.get(session_id, [])
        if limit is None:
            return list(entries)
        return entries[-limit:]

    async def get_history_page(
        self, session_id: str, *, before_id: int | None = None, query: str | None = None, limit: int = 50
    ) -> HistoryPage:
        entries = [
            entry
            for entry in reversed(self._store.get(session_id, []))
            if (before_id is None or (entry.id or 0) < before_id)
            and (not query or query.lower() in entry.content.lower())
        ]
        page = entries[:limit]
        return HistoryPage(entries=page, next_before_id=page[-1].id if len(entries) > limit else None)

    async def count_history(self, session_id: str) -> int:
        return len(self._store.get(session_id, []))

    async def list_sessions(
        self, *, query: str | None = None, cursor: str | None = None, limit: int = 50
    ) -> SessionPage:
        summaries = []
        for session_id, entries in self._store.items():
            matches = sum(query.lower() in entry.content.lower() for entry in entries) if query else None
            if not entries or (query and not matches and query.lower() not in session_id.lower()):
                continue
            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    message_count=len(entries),
                    last_activity=entries[-1].created_at,
                    match_count=matches,
                )
            )
        summaries.sort(key=lambda summary: (summary.last_activity, summary.session_id), reverse=True)
        if cursor:
            ids = [summary.session_id for summary in summaries]
            if cursor not in ids:
                raise ValueError("malformed session cursor")
            summaries = summaries[ids.index(cursor) + 1 :]
        page = summaries[:limit]
        return SessionPage(sessions=page, next_cursor=page[-1].session_id if len(summaries) > limit else None)

    async def trim_history(self, session_id: str, keep_latest: int) -> int:
        self.trim_calls.append((session_id, keep_latest))
        entries = self._store.get(session_id, [])
        if keep_latest <= 0:
            removed = len(entries)
            self._store[session_id] = []
            return removed
        if len(entries) <= keep_latest:
            return 0
        removed = len(entries) - keep_latest
        self._store[session_id] = entries[-keep_latest:]
        return removed
