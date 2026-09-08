from __future__ import annotations

import json
from typing import Any

import pytest

from minibot.adapters.config.schema import RabbitMQConsumerConfig
from minibot.adapters.messaging.rabbitmq.producer import RabbitMQTaskProducer
from minibot.core.tasks import TaskRequest


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
async def test_enqueue_publishes_persistent_json_to_fanout_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    exchange = _FakeExchange()
    channel_obj = _FakeChannel(exchange)
    connection = _FakeConnection(channel_obj)

    async def _connect(url: str) -> _FakeConnection:
        assert url == "amqp://broker/"
        return connection

    monkeypatch.setattr("minibot.adapters.messaging.rabbitmq.producer.aio_pika.connect_robust", _connect)
    monkeypatch.setattr("minibot.adapters.messaging.rabbitmq.producer.aio_pika.Message", _FakeMessage)

    producer = RabbitMQTaskProducer(RabbitMQConsumerConfig(broker_url="amqp://broker/"))
    await producer.enqueue(
        TaskRequest(
            task_id="task-1",
            channel="console",
            prompt="Summarize logs",
            agent_name="playwright_mcp_agent",
            context={"trace_id": "abc"},
            chat_id=42,
            user_id=7,
        )
    )

    assert connection.entered is True
    assert connection.exited is True
    assert channel_obj.declarations[0]["name"] == "minibot.tasks"
    assert channel_obj.declarations[0]["durable"] is True

    published = exchange.published[0]
    assert published["routing_key"] == ""
    assert published["message"].content_type == "application/json"
    # The consumer in service.py reads exactly these keys off the body.
    assert json.loads(published["message"].body) == {
        "task_id": "task-1",
        "channel": "console",
        "prompt": "Summarize logs",
        "agent_name": "playwright_mcp_agent",
        "context": {"trace_id": "abc"},
        "chat_id": 42,
        "user_id": 7,
    }
