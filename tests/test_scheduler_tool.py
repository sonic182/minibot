from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

import pytest

from minibot.core.jobs import PromptRole, ScheduledPrompt, ScheduledPromptStatus
from minibot.app.scheduler_service import ScheduledPromptService
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.scheduler import SchedulePromptTool


class StubPromptService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def schedule_prompt(self, **kwargs):  # type: ignore[override]
        self.calls.append(kwargs)
        run_at = kwargs["run_at"]
        return ScheduledPrompt(
            id="job-1",
            owner_id=str(kwargs.get("owner_id")),
            channel=str(kwargs.get("channel")),
            text=str(kwargs.get("text")),
            run_at=run_at,
            status=ScheduledPromptStatus.PENDING,
            chat_id=kwargs.get("chat_id"),
            user_id=kwargs.get("user_id"),
            role=kwargs.get("role", PromptRole.ASSISTANT),
            metadata=dict(kwargs.get("metadata") or {}),
        )


@pytest.mark.asyncio
async def test_schedule_prompt_tool_accepts_delay_seconds() -> None:
    service = StubPromptService()
    tool = SchedulePromptTool(cast(ScheduledPromptService, service))
    binding = tool.bindings()[0]
    context = ToolContext(owner_id="owner", channel="telegram", chat_id=1, user_id=2)
    result = await binding.handler({"content": "remind me", "delay_seconds": 5}, context)
    assert result["job_id"] == "job-1"
    assert result["status"] == ScheduledPromptStatus.PENDING.value
    assert service.calls
    scheduled_run = service.calls[0]["run_at"]
    assert isinstance(scheduled_run, datetime)
    assert scheduled_run >= datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_schedule_prompt_tool_requires_channel() -> None:
    service = StubPromptService()
    tool = SchedulePromptTool(cast(ScheduledPromptService, service))
    binding = tool.bindings()[0]
    context = ToolContext(owner_id="owner")
    with pytest.raises(ValueError):
        await binding.handler({"content": "hello", "delay_seconds": 5}, context)
