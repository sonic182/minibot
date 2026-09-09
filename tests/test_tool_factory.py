from __future__ import annotations

from typing import Any

import pytest
from llm_async.models import Tool

from minibot.adapters.config.schema import CalculatorToolConfig, Settings, ToolsConfig
from minibot.app.event_bus import EventBus
from minibot.app.extensions import load_extensions
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.factory import build_enabled_tools


class _MemoryStub:
    async def append_history(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content

    async def get_history(self, session_id: str, limit: int | None = None) -> list[object]:
        del session_id, limit
        return []

    async def count_history(self, session_id: str) -> int:
        del session_id
        return 0

    async def trim_history(self, session_id: str, keep_latest: int) -> int:
        del session_id, keep_latest
        return 0


def test_build_enabled_tools_keeps_only_core_tools_without_extensions() -> None:
    settings = Settings(tools=ToolsConfig(calculator=CalculatorToolConfig(enabled=True)))

    names = {binding.tool.name for binding in build_enabled_tools(settings, memory=_MemoryStub())}

    assert {"chat_history_info", "chat_history_trim", "calculate_expression"}.issubset(names)
    assert "current_datetime" not in names


def test_build_enabled_tools_merges_bundled_extension_tools() -> None:
    settings = Settings.from_dict({"tools": {"time": {"enabled": True}, "wait": {"enabled": True}}})
    registry = load_extensions(settings, EventBus(), entrypoint="console")

    names = {
        binding.tool.name
        for binding in build_enabled_tools(settings, memory=_MemoryStub(), extension_tools=registry.tools)
    }

    assert {"current_datetime", "wait"}.issubset(names)


def test_build_enabled_tools_rejects_extension_tool_name_collisions() -> None:
    settings = Settings()

    async def handler(payload: dict[str, Any], context: ToolContext) -> dict[str, bool]:
        del payload, context
        return {"ok": True}

    duplicate = ToolBinding(
        tool=Tool(name="calculate_expression", description="duplicate", parameters={}),
        handler=handler,
    )
    with pytest.raises(ValueError, match="duplicate tool name"):
        build_enabled_tools(settings, memory=_MemoryStub(), extension_tools=[duplicate])
