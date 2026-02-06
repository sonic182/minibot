from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

import pytest

from minibot.core.jobs import PromptRecurrence, PromptRole, ScheduledPrompt, ScheduledPromptStatus
from minibot.app.scheduler_service import ScheduledPromptService
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.scheduler import SchedulePromptTool


class StubPromptService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.cancel_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []

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
            role=kwargs.get("role", PromptRole.USER),
            metadata=dict(kwargs.get("metadata") or {}),
            recurrence=kwargs.get("recurrence", PromptRecurrence.NONE),
            recurrence_interval_seconds=kwargs.get("recurrence_interval_seconds"),
            recurrence_end_at=kwargs.get("recurrence_end_at"),
        )

    async def cancel_prompt(self, **kwargs):  # type: ignore[override]
        self.cancel_calls.append(kwargs)
        if kwargs.get("job_id") == "missing":
            return None
        return ScheduledPrompt(
            id=str(kwargs.get("job_id")),
            owner_id=str(kwargs.get("owner_id")),
            channel=str(kwargs.get("channel")),
            text="scheduled",
            run_at=datetime.now(timezone.utc),
            status=ScheduledPromptStatus.CANCELLED,
            chat_id=kwargs.get("chat_id"),
            user_id=kwargs.get("user_id"),
        )

    async def list_prompts(self, **kwargs):  # type: ignore[override]
        self.list_calls.append(kwargs)
        return [
            ScheduledPrompt(
                id="job-1",
                owner_id=str(kwargs.get("owner_id")),
                channel=str(kwargs.get("channel")),
                text="hello",
                run_at=datetime.now(timezone.utc),
                status=ScheduledPromptStatus.PENDING,
                chat_id=kwargs.get("chat_id"),
                user_id=kwargs.get("user_id"),
                recurrence=PromptRecurrence.INTERVAL,
                recurrence_interval_seconds=300,
            )
        ]


@pytest.mark.asyncio
async def test_schedule_prompt_tool_accepts_delay_seconds() -> None:
    service = StubPromptService()
    tool = SchedulePromptTool(cast(ScheduledPromptService, service))
    binding = tool.bindings()[0]
    context = ToolContext(owner_id="owner", channel="telegram", chat_id=1, user_id=2)
    result = await binding.handler({"content": "remind me", "delay_seconds": 5}, context)
    assert result["scheduled"] is True
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


@pytest.mark.asyncio
async def test_cancel_scheduled_prompt_tool_returns_cancelled_status() -> None:
    service = StubPromptService()
    tool = SchedulePromptTool(cast(ScheduledPromptService, service))
    bindings = {binding.tool.name: binding for binding in tool.bindings()}
    context = ToolContext(owner_id="owner", channel="telegram", chat_id=1, user_id=2)
    result = await bindings["cancel_scheduled_prompt"].handler({"job_id": "job-22"}, context)
    assert result["cancelled"] is True
    assert result["status"] == ScheduledPromptStatus.CANCELLED.value
    assert service.cancel_calls


@pytest.mark.asyncio
async def test_list_scheduled_prompts_tool_returns_jobs() -> None:
    service = StubPromptService()
    tool = SchedulePromptTool(cast(ScheduledPromptService, service))
    bindings = {binding.tool.name: binding for binding in tool.bindings()}
    context = ToolContext(owner_id="owner", channel="telegram", chat_id=1, user_id=2)
    result = await bindings["list_scheduled_prompts"].handler({"active_only": True, "limit": 5}, context)
    assert result["count"] == 1
    assert result["jobs"][0]["job_id"] == "job-1"
    assert result["jobs"][0]["recurrence"] == PromptRecurrence.INTERVAL.value
    assert service.list_calls


@pytest.mark.asyncio
async def test_schedule_prompt_tool_returns_validation_error_for_low_recurrence_interval() -> None:
    service = StubPromptService()
    tool = SchedulePromptTool(cast(ScheduledPromptService, service), min_recurrence_interval_seconds=60)
    binding = tool.bindings()[0]
    context = ToolContext(owner_id="owner", channel="telegram", chat_id=1, user_id=2)
    result = await binding.handler(
        {
            "content": "say hi",
            "delay_seconds": 5,
            "recurrence_type": "interval",
            "recurrence_interval_seconds": 15,
        },
        context,
    )
    assert result["scheduled"] is False
    assert "min_recurrence_interval_seconds" in result
