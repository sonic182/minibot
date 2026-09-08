import asyncio

import pytest

from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage, ChannelResponse
from minibot.core.events import MessageEvent, OutboundEvent


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


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_event_bus_respects_subscription_type_filter() -> None:
    bus = EventBus()
    filtered = bus.subscribe(types=(OutboundEvent,))
    unfiltered = bus.subscribe()

    await bus.publish(
        MessageEvent(message=ChannelMessage(channel="console", user_id=1, chat_id=2, message_id=3, text="hi"))
    )
    await bus.publish(OutboundEvent(response=ChannelResponse(channel="console", chat_id=2, text="pong")))

    assert filtered._queue.qsize() == 1
    assert unfiltered._queue.qsize() == 2

    seen = []
    async for event in filtered:
        seen.append(event)
        break
    assert isinstance(seen[0], OutboundEvent)

    await filtered.close()
    await unfiltered.close()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_lossy_subscriber_drops_instead_of_blocking_when_queue_is_full() -> None:
    bus = EventBus(maxsize=1)
    lossy = bus.subscribe(lossy=True)

    def _event(text: str) -> OutboundEvent:
        return OutboundEvent(response=ChannelResponse(channel="console", chat_id=1, text=text))

    await bus.publish(_event("one"))
    # Queue is now full. A blocking subscriber would hang here; a lossy one drops.
    await asyncio.wait_for(bus.publish(_event("two")), timeout=0.5)

    assert lossy._queue.qsize() == 1

    await lossy.close()
