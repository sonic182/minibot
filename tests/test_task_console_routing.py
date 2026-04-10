from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minibot.adapters.messaging.console.service import ConsoleService
from minibot.adapters.tasks.manager import TaskManager
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelResponse, RenderableResponse
from minibot.core.events import MessageEvent, OutboundEvent


class _PipeSuccess:
    def __init__(self, result: dict) -> None:
        self._result = result

    @asynccontextmanager
    async def open(self):
        result = self._result

        class _RX:
            async def readline(self) -> bytes:
                return json.dumps(result).encode() + b"\n"

        class _TX:
            def write(self, _data: bytes) -> None:
                pass

        yield _RX(), _TX()


@pytest.mark.asyncio
async def test_task_result_preserves_console_channel_for_outbound_routing() -> None:
    bus = EventBus()
    manager = TaskManager(event_bus=bus, worker_timeout_seconds=1.0)
    console = ConsoleService(bus)
    await console.start()

    sem = asyncio.Semaphore(1)
    await sem.acquire()
    ack_cb = AsyncMock()
    nack_cb = AsyncMock()
    relay_subscription = bus.subscribe()

    async def relay() -> None:
        async for event in relay_subscription:
            if not isinstance(event, MessageEvent):
                continue
            response = ChannelResponse(
                channel=event.message.channel,
                chat_id=event.message.chat_id or 0,
                text=f"handled: {event.message.text}",
                render=RenderableResponse(kind="text", text=f"handled: {event.message.text}"),
            )
            await bus.publish(OutboundEvent(response=response))
            break

    relay_task = asyncio.create_task(relay())

    with (
        patch("minibot.adapters.tasks.manager.aioduplex", return_value=(_PipeSuccess({"text": "done"}), MagicMock())),
        patch("minibot.adapters.tasks.manager.Process", return_value=MagicMock()),
    ):
        await manager.spawn(
            task_id="t1",
            channel="console",
            prompt="do work",
            context={},
            chat_id=1,
            user_id=2,
            ack_cb=ack_cb,
            nack_cb=nack_cb,
            semaphore=sem,
        )

    response = await console.wait_for_response(timeout_seconds=2.0)

    assert response.response.channel == "console"
    assert response.rendered_text == "handled: done"
    ack_cb.assert_called_once()
    nack_cb.assert_not_called()

    await relay_subscription.close()
    await relay_task
    await console.stop()
