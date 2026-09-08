from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from minibot.core.events import BaseEvent


@dataclass(frozen=True)
class _Subscriber:
    queue: asyncio.Queue[BaseEvent | None]
    types: tuple[type[BaseEvent], ...] | None

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
        await self._queue.put(None)
        self._bus._unsubscribe(self._queue)


class EventBus:
    def __init__(self, maxsize: int = 128) -> None:
        self._subscribers: list[_Subscriber] = []
        self._maxsize = maxsize
        self._closed = False

    def subscribe(self, types: tuple[type[BaseEvent], ...] | None = None) -> EventSubscription:
        """Subscribe to the bus, optionally narrowing delivery to ``types``.

        A subscription with ``types=None`` receives every event. Narrowing matters for
        high-volume events (turn/tool lifecycle): a non-matching event never occupies a
        slot in this subscriber's bounded queue.
        """
        queue: asyncio.Queue[BaseEvent | None] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.append(_Subscriber(queue=queue, types=types))
        return EventSubscription(queue, self)

    async def publish(self, event: BaseEvent) -> None:
        if self._closed:
            raise RuntimeError("event bus is stopped")
        queues = [subscriber.queue for subscriber in list(self._subscribers) if subscriber.wants(event)]
        if not queues:
            return
        await asyncio.gather(*(queue.put(event) for queue in queues))

    async def stop(self) -> None:
        self._closed = True
        for subscriber in list(self._subscribers):
            await subscriber.queue.put(None)

    def _unsubscribe(self, queue: asyncio.Queue[BaseEvent | None]) -> None:
        self._subscribers = [subscriber for subscriber in self._subscribers if subscriber.queue is not queue]
