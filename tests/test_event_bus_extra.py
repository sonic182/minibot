from __future__ import annotations

import pytest

from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent


def _event() -> MessageEvent:
    return MessageEvent(
        message=ChannelMessage(channel="telegram", user_id=1, chat_id=2, message_id=3, text="ping"),
    )


@pytest.mark.asyncio
async def test_event_bus_rejects_publish_after_stop() -> None:
    bus = EventBus()
    await bus.stop()

    with pytest.raises(RuntimeError):
        await bus.publish(_event())


@pytest.mark.asyncio
async def test_event_subscription_close_stops_iteration() -> None:
    bus = EventBus()
    subscription = bus.subscribe()
    await subscription.close()

    items = []
    async for item in subscription:
        items.append(item)

    assert items == []
