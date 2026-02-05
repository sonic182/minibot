from __future__ import annotations

import asyncio
from typing import AsyncIterator, List

from ..core.events import BaseEvent


class EventSubscription:
    def __init__(self, queue: asyncio.Queue[BaseEvent | None], bus: "EventBus") -> None:
        self._queue = queue
        self._bus = bus

    async def __aiter__(self) -> AsyncIterator[BaseEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                break
            yield event
            self._queue.task_done()

    async def close(self) -> None:
        await self._queue.put(None)
        self._bus._unsubscribe(self._queue)


class EventBus:
    def __init__(self, maxsize: int = 128) -> None:
        self._subscribers: List[asyncio.Queue[BaseEvent | None]] = []
        self._maxsize = maxsize
        self._closed = False

    def subscribe(self) -> EventSubscription:
        queue: asyncio.Queue[BaseEvent | None] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.append(queue)
        return EventSubscription(queue, self)

    async def publish(self, event: BaseEvent) -> None:
        if self._closed:
            raise RuntimeError("event bus is stopped")
        await asyncio.gather(*(queue.put(event) for queue in list(self._subscribers)))

    async def stop(self) -> None:
        self._closed = True
        for queue in list(self._subscribers):
            await queue.put(None)

    def _unsubscribe(self, queue: asyncio.Queue[BaseEvent | None]) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass
