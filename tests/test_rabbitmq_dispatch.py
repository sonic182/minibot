from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from minibot.adapters.config.schema import RabbitMQConsumerConfig
from minibot.adapters.messaging.rabbitmq.service import RabbitMQConsumerService
from minibot.app.event_bus import EventBus


def _make_config() -> RabbitMQConsumerConfig:
    return RabbitMQConsumerConfig(
        enabled=True,
        broker_url="amqp://guest:guest@localhost/",
        queue_name="test.worker",
        exchange_name="test.exchange",
        prefetch_count=1,
        worker_timeout_seconds=5,
        max_concurrent_workers=4,
    )


def _make_service(task_manager=None) -> RabbitMQConsumerService:
    return RabbitMQConsumerService(_make_config(), EventBus(), task_manager)


def _make_message(body: bytes) -> MagicMock:
    msg = MagicMock()
    msg.body = body
    msg.nack = AsyncMock()
    msg.ack = AsyncMock()
    msg.routing_key = "test"
    msg.correlation_id = None
    msg.reply_to = None
    return msg


# ---------------------------------------------------------------------------
# Validation: malformed bodies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_invalid_json_nacks_and_discards() -> None:
    service = _make_service()
    msg = _make_message(b"not-json")

    await service._dispatch(msg)

    msg.nack.assert_called_once_with(requeue=False)
    msg.ack.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_missing_task_id_nacks_and_discards() -> None:
    service = _make_service()
    msg = _make_message(json.dumps({"channel": "console", "prompt": "hello"}).encode())

    await service._dispatch(msg)

    msg.nack.assert_called_once_with(requeue=False)
    msg.ack.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_missing_prompt_nacks_and_discards() -> None:
    service = _make_service()
    msg = _make_message(json.dumps({"task_id": "t1", "channel": "console"}).encode())

    await service._dispatch(msg)

    msg.nack.assert_called_once_with(requeue=False)
    msg.ack.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_missing_channel_nacks_and_discards() -> None:
    service = _make_service()
    msg = _make_message(json.dumps({"task_id": "t1", "prompt": "hello"}).encode())

    await service._dispatch(msg)

    msg.nack.assert_called_once_with(requeue=False)
    msg.ack.assert_not_called()


# ---------------------------------------------------------------------------
# No task manager configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_no_task_manager_nacks_and_discards() -> None:
    service = _make_service(task_manager=None)
    msg = _make_message(json.dumps({"task_id": "t1", "channel": "console", "prompt": "hello"}).encode())

    await service._dispatch(msg)

    msg.nack.assert_called_once_with(requeue=False)
    msg.ack.assert_not_called()


# ---------------------------------------------------------------------------
# Valid message delegates to TaskManager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_valid_message_delegates_to_task_manager() -> None:
    task_manager = MagicMock()
    task_manager.spawn = AsyncMock()
    service = _make_service(task_manager=task_manager)

    body = {
        "task_id": "t1",
        "channel": "telegram",
        "prompt": "summarise this",
        "agent_name": "playwright_mcp_agent",
        "chat_id": 10,
        "user_id": 20,
        "context": {"k": "v"},
    }
    msg = _make_message(json.dumps(body).encode())

    await service._dispatch(msg)

    task_manager.spawn.assert_called_once()
    kw = task_manager.spawn.call_args.kwargs
    assert kw["task_id"] == "t1"
    assert kw["channel"] == "telegram"
    assert kw["prompt"] == "summarise this"
    assert kw["agent_name"] == "playwright_mcp_agent"
    assert kw["chat_id"] == 10
    assert kw["user_id"] == 20
    assert kw["context"] == {"k": "v"}


@pytest.mark.asyncio
async def test_dispatch_optional_fields_default_to_none() -> None:
    task_manager = MagicMock()
    task_manager.spawn = AsyncMock()
    service = _make_service(task_manager=task_manager)

    body = {"task_id": "t1", "channel": "console", "prompt": "hello"}
    msg = _make_message(json.dumps(body).encode())

    await service._dispatch(msg)

    kw = task_manager.spawn.call_args.kwargs
    assert kw["channel"] == "console"
    assert kw["agent_name"] is None
    assert kw["chat_id"] is None
    assert kw["user_id"] is None
    assert kw["context"] == {}
