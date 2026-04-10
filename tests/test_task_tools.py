from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from minibot.adapters.config.schema import RabbitMQConsumerConfig
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


class _FakeMessage:
    def __init__(self, *, body: bytes, content_type: str, delivery_mode: Any) -> None:
        self.body = body
        self.content_type = content_type
        self.delivery_mode = delivery_mode


class _FakeExchange:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, message: _FakeMessage, routing_key: str) -> None:
        self.published.append({"message": message, "routing_key": routing_key})


class _FakeChannel:
    def __init__(self, exchange: _FakeExchange) -> None:
        self.exchange = exchange
        self.declarations: list[dict[str, Any]] = []

    async def declare_exchange(self, name: str, exchange_type: Any, durable: bool) -> _FakeExchange:
        self.declarations.append({"name": name, "exchange_type": exchange_type, "durable": durable})
        return self.exchange


class _FakeConnection:
    def __init__(self, channel_obj: _FakeChannel) -> None:
        self.channel_obj = channel_obj
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> _FakeConnection:
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        self.exited = True

    async def channel(self) -> _FakeChannel:
        return self.channel_obj


@pytest.mark.asyncio
async def test_spawn_task_publishes_rabbitmq_message(monkeypatch: pytest.MonkeyPatch) -> None:
    task_manager = _TaskManagerStub()
    tools = TaskTools(RabbitMQConsumerConfig(enabled=True, broker_url="amqp://broker/"), cast(Any, task_manager))
    bindings = {binding.tool.name: binding for binding in tools.bindings()}
    exchange = _FakeExchange()
    channel_obj = _FakeChannel(exchange)
    connection = _FakeConnection(channel_obj)

    async def _connect(url: str) -> _FakeConnection:
        assert url == "amqp://broker/"
        return connection

    monkeypatch.setattr("minibot.llm.tools.tasks.aio_pika.connect_robust", _connect)
    monkeypatch.setattr("minibot.llm.tools.tasks.aio_pika.Message", _FakeMessage)

    result = await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "agent_name": "playwright_mcp_agent", "context_json": '{"trace_id":"abc"}'},
        ToolContext(channel="console", chat_id=42, user_id=7),
    )

    assert result["status"] == "queued"
    assert result["channel"] == "console"
    assert connection.entered is True
    assert connection.exited is True
    assert channel_obj.declarations[0]["name"] == "minibot.tasks"
    published = exchange.published[0]
    assert published["routing_key"] == ""
    assert published["message"].content_type == "application/json"
    payload = published["message"].body.decode()
    assert '"channel": "console"' in payload
    assert '"chat_id": 42' in payload
    assert '"user_id": 7' in payload
    assert '"prompt": "Summarize logs"' in payload
    assert '"agent_name": "playwright_mcp_agent"' in payload
    assert '"trace_id": "abc"' in payload


@pytest.mark.asyncio
async def test_spawn_task_accepts_legacy_context_object(monkeypatch: pytest.MonkeyPatch) -> None:
    task_manager = _TaskManagerStub()
    tools = TaskTools(RabbitMQConsumerConfig(enabled=True, broker_url="amqp://broker/"), cast(Any, task_manager))
    bindings = {binding.tool.name: binding for binding in tools.bindings()}
    exchange = _FakeExchange()
    channel_obj = _FakeChannel(exchange)
    connection = _FakeConnection(channel_obj)

    async def _connect(url: str) -> _FakeConnection:
        assert url == "amqp://broker/"
        return connection

    monkeypatch.setattr("minibot.llm.tools.tasks.aio_pika.connect_robust", _connect)
    monkeypatch.setattr("minibot.llm.tools.tasks.aio_pika.Message", _FakeMessage)

    await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "context": {"trace_id": "abc"}},
        ToolContext(channel="console", chat_id=42, user_id=7),
    )

    payload = exchange.published[0]["message"].body.decode()
    assert '"trace_id": "abc"' in payload


@pytest.mark.asyncio
async def test_cancel_task_returns_cancelled_flag() -> None:
    task_manager = _TaskManagerStub()
    task_manager.cancel_result = True
    tools = TaskTools(RabbitMQConsumerConfig(enabled=True), cast(Any, task_manager))
    bindings = {binding.tool.name: binding for binding in tools.bindings()}

    result = await bindings["cancel_task"].handler({"task_id": "task-1"}, ToolContext())

    assert result == {"task_id": "task-1", "cancelled": True}
    assert task_manager.cancel_calls == ["task-1"]


@pytest.mark.asyncio
async def test_list_tasks_returns_active_tasks() -> None:
    task_manager = _TaskManagerStub()
    task_manager.active_tasks = [_TaskStub("task-1", "telegram", datetime.now(UTC))]
    tools = TaskTools(RabbitMQConsumerConfig(enabled=True), cast(Any, task_manager))
    bindings = {binding.tool.name: binding for binding in tools.bindings()}

    result = await bindings["list_tasks"].handler({}, ToolContext())

    assert result["count"] == 1
    assert result["tasks"][0]["task_id"] == "task-1"
    assert result["tasks"][0]["channel"] == "telegram"
    assert isinstance(result["tasks"][0]["started_at"], str)
