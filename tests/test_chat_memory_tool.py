from __future__ import annotations

from datetime import datetime, timezone

import pytest

from minibot.core.memory import MemoryEntry
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.chat_memory import ChatMemoryTool
from minibot.shared.utils import session_id_from_parts


class StubMemory:
    def __init__(self) -> None:
        self._store: dict[str, list[MemoryEntry]] = {}

    async def append_history(self, session_id: str, role: str, content: str) -> None:
        entry = MemoryEntry(role=role, content=content, created_at=datetime.now(timezone.utc))
        self._store.setdefault(session_id, []).append(entry)

    async def get_history(self, session_id: str, limit: int = 32) -> list[MemoryEntry]:
        return self._store.get(session_id, [])[-limit:]

    async def count_history(self, session_id: str) -> int:
        return len(self._store.get(session_id, []))

    async def trim_history(self, session_id: str, keep_latest: int) -> int:
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


def _tool_map(memory: StubMemory) -> dict[str, ToolBinding]:
    return {binding.tool.name: binding for binding in ChatMemoryTool(memory).bindings()}


@pytest.mark.asyncio
async def test_chat_memory_info_and_trim() -> None:
    memory = StubMemory()
    tools = _tool_map(memory)
    context = ToolContext(channel="telegram", chat_id=100, user_id=1)

    info_binding = tools["chat_memory_info"]
    trim_binding = tools["chat_memory_trim"]
    session_id = session_id_from_parts("telegram", 100, 1)

    await memory.append_history(session_id, "user", "one")
    await memory.append_history(session_id, "assistant", "two")
    await memory.append_history(session_id, "user", "three")

    info_result = await info_binding.handler({}, context)
    assert info_result["total_messages"] == 3

    trim_result = await trim_binding.handler({"keep_latest": 1}, context)
    assert trim_result["removed_messages"] == 2
    assert trim_result["remaining_messages"] == 1


@pytest.mark.asyncio
async def test_chat_memory_tool_requires_channel_context() -> None:
    memory = StubMemory()
    tools = _tool_map(memory)
    with pytest.raises(ValueError):
        await tools["chat_memory_info"].handler({}, ToolContext())
