from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import NotRequired, TypedDict

from minibot.app.event_bus import EventBus, EventSubscription
from minibot.core.channels import ChannelMessage
from minibot.core.events import (
    MessageEvent,
    OutboundEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)

_LIVE_QUEUE_LIMIT = 256


class ChatMessageEvent(TypedDict):
    role: str
    text: str


class ChatStateEvent(TypedDict):
    busy: bool
    error: NotRequired[str]


type ChatEvent = ChatMessageEvent | ChatStateEvent


@dataclass(eq=False)
class WebChatSubscription:
    events: asyncio.Queue[ChatMessageEvent]
    state: asyncio.Queue[ChatStateEvent]


def _offer[T](queue: asyncio.Queue[T], item: T) -> bool:
    dropped = False
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
            queue.task_done()
            dropped = True
    queue.put_nowait(item)
    return dropped


def _replace_state(queue: asyncio.Queue[ChatStateEvent], item: ChatStateEvent) -> None:
    with contextlib.suppress(asyncio.QueueEmpty):
        queue.get_nowait()
        queue.task_done()
    queue.put_nowait(item)


class WebChannelService:
    """Fan out one web chat session to its active WebSocket connections."""

    def __init__(self, event_bus: EventBus, *, chat_id: int = 1, user_id: int = 1) -> None:
        self._event_bus = event_bus
        self._chat_id = chat_id
        self._user_id = user_id
        self._logger = logging.getLogger("minibot.web")
        self._message_id = 0
        self._subscription: EventSubscription = event_bus.subscribe(
            types=(OutboundEvent, TurnStartedEvent, TurnCompletedEvent, TurnFailedEvent)
        )
        self._outgoing_task: asyncio.Task[None] | None = None
        self._busy = False
        self._subscribers: set[WebChatSubscription] = set()

    async def start(self) -> None:
        if self._outgoing_task is None or self._outgoing_task.done():
            self._outgoing_task = asyncio.create_task(self._consume_outgoing())

    async def stop(self) -> None:
        await self._subscription.close()
        if self._outgoing_task is not None:
            self._outgoing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._outgoing_task
        self._outgoing_task = None
        self._subscribers.clear()

    async def publish_user_message(self, text: str) -> None:
        self._message_id += 1
        message = ChannelMessage(
            channel="web",
            user_id=self._user_id,
            chat_id=self._chat_id,
            message_id=self._message_id,
            text=text,
        )
        self._broadcast({"role": "user", "text": text})
        await self._event_bus.publish(MessageEvent(message=message))

    def subscribe(self) -> WebChatSubscription:
        subscription = WebChatSubscription(
            events=asyncio.Queue(maxsize=_LIVE_QUEUE_LIMIT),
            state=asyncio.Queue(maxsize=1),
        )
        _replace_state(subscription.state, {"busy": self._busy})
        self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: WebChatSubscription) -> None:
        self._subscribers.discard(subscription)

    async def _consume_outgoing(self) -> None:
        async for event in self._subscription:
            try:
                if isinstance(event, OutboundEvent) and event.response.channel == "web":
                    render = event.response.render
                    text = render.text if render is not None else event.response.text
                    self._broadcast({"role": "assistant", "text": text})
                elif isinstance(event, TurnStartedEvent) and event.channel == "web":
                    self._set_busy(True)
                elif isinstance(event, TurnCompletedEvent) and event.channel == "web":
                    self._set_busy(False)
                elif isinstance(event, TurnFailedEvent) and event.channel == "web":
                    self._set_busy(False, error=event.error)
            except Exception:
                self._logger.exception("web outbound event failed", extra={"event_type": event.event_type})

    def _broadcast(self, event: ChatEvent) -> None:
        if "busy" in event:
            for subscription in tuple(self._subscribers):
                _replace_state(subscription.state, event)
            return
        dropped = sum(_offer(subscription.events, event) for subscription in tuple(self._subscribers))
        if dropped:
            self._logger.warning("web live events dropped", extra={"component": "web", "count": dropped})

    def _set_busy(self, busy: bool, *, error: str | None = None) -> None:
        self._busy = busy
        event: ChatStateEvent = {"busy": busy}
        if error is not None:
            event["error"] = error
        self._broadcast(event)
