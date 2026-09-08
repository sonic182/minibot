from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from minibot.core.events import BaseEvent

_logger = logging.getLogger("minibot.event_bus")


def _put_sentinel(queue: asyncio.Queue[BaseEvent | None]) -> None:
    """Deliver the stop sentinel without ever blocking.

    A blocking ``put`` would deadlock shutdown whenever the queue is already full —
    which is exactly the state a slow or stalled subscriber leaves it in. Pending
    events no longer matter at this point, so evict to make room.
    """
    while True:
        try:
            queue.put_nowait(None)
            return
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:  # pragma: no cover - racing consumer drained it
                continue


@dataclass(frozen=True)
class _Subscriber:
    queue: asyncio.Queue[BaseEvent | None]
    types: tuple[type[BaseEvent], ...] | None
    lossy: bool = False

    def wants(self, event: BaseEvent) -> bool:
        return self.types is None or isinstance(event, self.types)


class EventSubscription:
    def __init__(self, queue: asyncio.Queue[BaseEvent | None], bus: EventBus) -> None:
        self._queue = queue
        self._bus = bus

    async def __aiter__(self) -> AsyncIterator[BaseEvent]:
        while True:
            event = await self._queue.get()
            self._queue.task_done()
            if event is None:
                break
            yield event

    async def close(self) -> None:
        _put_sentinel(self._queue)
        self._bus._unsubscribe(self._queue)


class EventBus:
    def __init__(self, maxsize: int = 128) -> None:
        self._subscribers: list[_Subscriber] = []
        self._maxsize = maxsize
        self._closed = False

    def subscribe(
        self,
        types: tuple[type[BaseEvent], ...] | None = None,
        *,
        lossy: bool = False,
    ) -> EventSubscription:
        """Subscribe to the bus, optionally narrowing delivery to ``types``.

        A subscription with ``types=None`` receives every event. Narrowing matters for
        high-volume events (turn/tool lifecycle): a non-matching event never occupies a
        slot in this subscriber's bounded queue.

        ``lossy=True`` drops events instead of applying back-pressure once the queue is
        full, so a slow subscriber cannot stall the bus for everyone else. Core
        subscribers stay blocking; extensions are lossy.
        """
        queue: asyncio.Queue[BaseEvent | None] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.append(_Subscriber(queue=queue, types=types, lossy=lossy))
        return EventSubscription(queue, self)

    async def publish(self, event: BaseEvent) -> None:
        if self._closed:
            raise RuntimeError("event bus is stopped")
        blocking: list[asyncio.Queue[BaseEvent | None]] = []
        for subscriber in list(self._subscribers):
            if not subscriber.wants(event):
                continue
            if not subscriber.lossy:
                blocking.append(subscriber.queue)
                continue
            try:
                subscriber.queue.put_nowait(event)
            except asyncio.QueueFull:
                _logger.warning(
                    "dropping event for lossy subscriber with a full queue",
                    extra={"event_type": event.event_type, "maxsize": self._maxsize},
                )
        if not blocking:
            return
        await asyncio.gather(*(queue.put(event) for queue in blocking))

    async def stop(self) -> None:
        self._closed = True
        for subscriber in list(self._subscribers):
            _put_sentinel(subscriber.queue)

    def _unsubscribe(self, queue: asyncio.Queue[BaseEvent | None]) -> None:
        self._subscribers = [subscriber for subscriber in self._subscribers if subscriber.queue is not queue]
