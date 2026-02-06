from __future__ import annotations

import pytest

from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.time import CurrentTimeTool


@pytest.mark.asyncio
async def test_current_time_tool_returns_timestamp() -> None:
    tool = CurrentTimeTool()
    binding = tool.bindings()[0]
    result = await binding.handler({}, ToolContext())
    assert "timestamp" in result
    assert isinstance(result["timestamp"], str)


@pytest.mark.asyncio
async def test_current_time_tool_accepts_format() -> None:
    tool = CurrentTimeTool()
    binding = tool.bindings()[0]
    fmt = "%Y"
    result = await binding.handler({"format": fmt}, ToolContext())
    assert result["timestamp"].isdigit()
