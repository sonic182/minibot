from __future__ import annotations

import pytest

from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.chat_memory import ChatMemoryTool
from minibot.shared.utils import session_identifier
from tests.fixtures.memory import InMemoryMemoryStore as StubMemory


def _tool_map(memory: StubMemory) -> dict[str, ToolBinding]:
    return {binding.tool.name: binding for binding in ChatMemoryTool(memory).bindings()}


@pytest.mark.asyncio
async def test_chat_memory_info_and_trim() -> None:
    memory = StubMemory()
    tools = _tool_map(memory)
    context = ToolContext(channel="telegram", chat_id=100, user_id=1)

    info_binding = tools["chat_history_info"]
    trim_binding = tools["chat_history_trim"]
    session_id = session_identifier("telegram", 100)

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
        await tools["chat_history_info"].handler({}, ToolContext())
