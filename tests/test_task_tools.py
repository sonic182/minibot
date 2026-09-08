from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from minibot.app.agent_registry import AgentRegistry
from minibot.core.agents import AgentSpec
from minibot.core.tasks import TaskRequest
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.tasks import TaskTools


class _TaskManagerStub:
    def __init__(self) -> None:
        self.cancel_calls: list[str] = []
        self.cancel_result = False
        self.active_tasks: list[Any] = []

    async def cancel(self, task_id: str) -> bool:
        self.cancel_calls.append(task_id)
        return self.cancel_result

    def active(self) -> list[Any]:
        return list(self.active_tasks)


class _TaskStub:
    def __init__(self, task_id: str, channel: str, started_at: datetime) -> None:
        self.task_id = task_id
        self.channel = channel
        self.started_at = started_at


class _ProducerStub:
    def __init__(self) -> None:
        self.enqueued: list[TaskRequest] = []

    async def enqueue(self, task: TaskRequest) -> None:
        self.enqueued.append(task)


def _build_tools(
    producer: _ProducerStub,
    task_manager: _TaskManagerStub,
    agent_registry: AgentRegistry | None = None,
) -> dict[str, Any]:
    tools = TaskTools(cast(Any, producer), cast(Any, task_manager), agent_registry=agent_registry)
    return {binding.tool.name: binding for binding in tools.bindings()}


@pytest.mark.asyncio
async def test_spawn_task_enqueues_request_and_reports_queued() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub())

    result = await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "agent_name": "playwright_mcp_agent", "context_json": '{"trace_id":"abc"}'},
        ToolContext(channel="console", chat_id=42, user_id=7),
    )

    assert result["status"] == "queued"
    assert result["channel"] == "console"
    assert result["chat_id"] == 42
    assert result["user_id"] == 7
    assert result["agent_name"] == "playwright_mcp_agent"

    enqueued = producer.enqueued[0]
    assert enqueued.task_id == result["task_id"]
    assert enqueued.channel == "console"
    assert enqueued.prompt == "Summarize logs"
    assert enqueued.agent_name == "playwright_mcp_agent"
    assert enqueued.context == {"trace_id": "abc"}
    assert enqueued.chat_id == 42
    assert enqueued.user_id == 7


@pytest.mark.asyncio
async def test_spawn_task_accepts_legacy_context_object() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub())

    await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "context": {"trace_id": "abc"}},
        ToolContext(channel="console", chat_id=42, user_id=7),
    )

    assert producer.enqueued[0].context == {"trace_id": "abc"}


@pytest.mark.asyncio
async def test_spawn_task_rejects_invalid_context_json() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub())

    with pytest.raises(ValueError, match="context_json must be valid JSON"):
        await bindings["spawn_task"].handler(
            {"prompt": "Summarize logs", "context_json": "not json"},
            ToolContext(channel="console"),
        )

    assert producer.enqueued == []


@pytest.mark.asyncio
async def test_spawn_task_rejects_unregistered_agent_name() -> None:
    producer = _ProducerStub()
    registry = AgentRegistry(
        [AgentSpec(name="general_agent", description="", system_prompt="", source_path=Path("agents/general.md"))]
    )
    bindings = _build_tools(producer, _TaskManagerStub(), agent_registry=registry)

    with pytest.raises(ValueError, match="agent_name 'general' is not a registered agent"):
        await bindings["spawn_task"].handler(
            {"prompt": "Summarize logs", "agent_name": "general"},
            ToolContext(channel="console"),
        )

    assert producer.enqueued == []


@pytest.mark.asyncio
async def test_spawn_task_accepts_registered_agent_name() -> None:
    producer = _ProducerStub()
    registry = AgentRegistry(
        [AgentSpec(name="general_agent", description="", system_prompt="", source_path=Path("agents/general.md"))]
    )
    bindings = _build_tools(producer, _TaskManagerStub(), agent_registry=registry)

    result = await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "agent_name": "general_agent"},
        ToolContext(channel="console"),
    )

    assert result["agent_name"] == "general_agent"
    assert producer.enqueued[0].agent_name == "general_agent"


@pytest.mark.asyncio
async def test_cancel_task_returns_cancelled_flag() -> None:
    task_manager = _TaskManagerStub()
    task_manager.cancel_result = True
    bindings = _build_tools(_ProducerStub(), task_manager)

    result = await bindings["cancel_task"].handler({"task_id": "task-1"}, ToolContext())

    assert result == {"task_id": "task-1", "cancelled": True}
    assert task_manager.cancel_calls == ["task-1"]


@pytest.mark.asyncio
async def test_list_tasks_returns_active_tasks() -> None:
    task_manager = _TaskManagerStub()
    task_manager.active_tasks = [_TaskStub("task-1", "telegram", datetime.now(UTC))]
    bindings = _build_tools(_ProducerStub(), task_manager)

    result = await bindings["list_tasks"].handler({}, ToolContext())

    assert result["count"] == 1
    assert result["tasks"][0]["task_id"] == "task-1"
    assert result["tasks"][0]["channel"] == "telegram"
    assert isinstance(result["tasks"][0]["started_at"], str)
