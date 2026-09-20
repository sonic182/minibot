from __future__ import annotations

import asyncio

import pytest
from llm_async.models import Tool

from minibot.app.event_bus import EventBus
from minibot.core.events import ToolCallEvent
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.tool_events import apply_tool_call_events


async def _successful_tool(_: dict[str, object], __: ToolContext) -> dict[str, bool]:
    return {"ok": True}


@pytest.mark.asyncio
async def test_tool_lifecycle_events_share_an_invocation_id() -> None:
    event_bus = EventBus()
    subscription = event_bus.subscribe(types=(ToolCallEvent,))
    binding = ToolBinding(
        tool=Tool(name="read_file", description="read a file", parameters={}),
        handler=_successful_tool,
    )
    wrapped = apply_tool_call_events([binding], event_bus=event_bus)[0]
    try:
        await wrapped.handler({}, ToolContext(channel="web", turn_id="turn-1"))
        await wrapped.handler({}, ToolContext(channel="web", turn_id="turn-1"))
        events = [await asyncio.wait_for(anext(subscription.__aiter__()), timeout=1) for _ in range(4)]
        assert [event.phase for event in events] == ["started", "completed", "started", "completed"]
        assert events[0].call_id == events[1].call_id
        assert events[2].call_id == events[3].call_id
        assert events[0].call_id != events[2].call_id
    finally:
        await subscription.close()
