import asyncio

import pytest

from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent


@pytest.mark.asyncio
async def test_event_bus_dispatches_to_subscribers() -> None:
    bus = EventBus()
    sub1 = bus.subscribe()
    sub2 = bus.subscribe()

    message = ChannelMessage(
        channel="telegram",
        user_id=1,
        chat_id=2,
        message_id=3,
        text="hi",
    )
    event = MessageEvent(message=message)

    await bus.publish(event)

    seen = []

    async def drain(subscription):
        async for item in subscription:
            seen.append(item)
            break

    await asyncio.gather(drain(sub1), drain(sub2))
    assert len(seen) == 2
    assert all(item.message.text == "hi" for item in seen)

    await sub1.close()
    await sub2.close()
