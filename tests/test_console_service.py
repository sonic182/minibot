from __future__ import annotations

import asyncio

import pytest

from minibot.app.event_bus import EventBus
from minibot.adapters.messaging.console.service import ConsoleService
from minibot.core.channels import ChannelResponse, RenderableResponse
from minibot.core.events import MessageEvent, OutboundEvent


class _CollectConsole:
    def __init__(self) -> None:
        self.printed: list[str] = []

    def print(self, value) -> None:
        self.printed.append(str(value))


@pytest.mark.asyncio
async def test_console_service_publishes_message_events() -> None:
    bus = EventBus()
    service = ConsoleService(bus, chat_id=11, user_id=22, console=_CollectConsole())
    subscription = bus.subscribe()
    await service.publish_user_message("hello")

    event = await asyncio.wait_for(subscription._queue.get(), timeout=0.5)
    assert isinstance(event, MessageEvent)
    assert event.message.channel == "console"
    assert event.message.chat_id == 11
    assert event.message.user_id == 22
    assert event.message.text == "hello"
    await subscription.close()


@pytest.mark.asyncio
async def test_console_service_consumes_console_outbound_events() -> None:
    bus = EventBus()
    console = _CollectConsole()
    service = ConsoleService(bus, chat_id=1, user_id=1, console=console)
    await service.start()

    await bus.publish(
        OutboundEvent(
            response=ChannelResponse(
                channel="console",
                chat_id=1,
                text="ok",
                render=RenderableResponse(kind="text", text="ok"),
            )
        )
    )

    item = await service.wait_for_response(1.0)
    assert item.response.channel == "console"
    assert item.rendered_text == "ok"
    assert console.printed
    await service.stop()


@pytest.mark.asyncio
async def test_console_service_ignores_non_console_outbound_events() -> None:
    bus = EventBus()
    console = _CollectConsole()
    service = ConsoleService(bus, chat_id=1, user_id=1, console=console)
    await service.start()

    await bus.publish(
        OutboundEvent(
            response=ChannelResponse(
                channel="telegram",
                chat_id=1,
                text="ignore",
                render=RenderableResponse(kind="text", text="ignore"),
            )
        )
    )

    with pytest.raises(asyncio.TimeoutError):
        await service.wait_for_response(0.1)
    assert console.printed == []
    await service.stop()
