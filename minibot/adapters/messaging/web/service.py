from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from minibot.app.event_bus import EventBus, EventSubscription
from minibot.core.channels import ChannelMessage
from minibot.core.events import (
    MessageEvent,
    OutboundEvent,
    ToolCallEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)

_LIVE_QUEUE_LIMIT = 256

type ChatEvent = dict[str, Any]


def _offer(queue: asyncio.Queue[ChatEvent], item: ChatEvent) -> None:
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
            queue.task_done()
    with contextlib.suppress(asyncio.QueueFull):
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
        self._tool_call_subscription: EventSubscription = event_bus.subscribe(types=(ToolCallEvent,), lossy=True)
        self._outgoing_task: asyncio.Task[None] | None = None
        self._tool_call_task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[ChatEvent]] = set()

    async def start(self) -> None:
        if self._outgoing_task is None or self._outgoing_task.done():
            self._outgoing_task = asyncio.create_task(self._consume_outgoing())
        if self._tool_call_task is None or self._tool_call_task.done():
            self._tool_call_task = asyncio.create_task(self._consume_tool_calls())

    async def stop(self) -> None:
        await self._subscription.close()
        await self._tool_call_subscription.close()
        for task in (self._outgoing_task, self._tool_call_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._outgoing_task = None
        self._tool_call_task = None
        self._subscribers.clear()

    async def publish_user_message(
        self,
        text: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
        incoming_files: list[dict[str, Any]] | None = None,
        attachment_display: list[dict[str, Any]] | None = None,
    ) -> None:
        self._message_id += 1
        message = ChannelMessage(
            channel="web",
            user_id=self._user_id,
            chat_id=self._chat_id,
            message_id=self._message_id,
            text=text,
            attachments=attachments or [],
            metadata={"incoming_files": incoming_files or []} if incoming_files else {},
        )
        event: ChatEvent = {"role": "user", "text": text}
        if attachment_display:
            event["attachments"] = attachment_display
        self._broadcast(event)
        await self._event_bus.publish(MessageEvent(message=message))

    def subscribe(self) -> asyncio.Queue[ChatEvent]:
        queue: asyncio.Queue[ChatEvent] = asyncio.Queue(maxsize=_LIVE_QUEUE_LIMIT)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[ChatEvent]) -> None:
        self._subscribers.discard(queue)

    async def _consume_outgoing(self) -> None:
        async for event in self._subscription:
            try:
                if isinstance(event, OutboundEvent) and event.response.channel == "web":
                    render = event.response.render
                    text = render.text if render is not None else event.response.text
                    self._broadcast({"role": "assistant", "text": text})
                elif isinstance(event, TurnStartedEvent) and event.channel == "web":
                    self._broadcast({"kind": "turn_started", "turn_id": event.turn_id, "busy": True})
                elif isinstance(event, TurnCompletedEvent) and event.channel == "web":
                    self._broadcast({"kind": "turn_completed", "turn_id": event.turn_id, "busy": False})
                elif isinstance(event, TurnFailedEvent) and event.channel == "web":
                    self._broadcast({"busy": False, "error": event.error})
            except Exception:
                self._logger.exception("web outbound event failed", extra={"event_type": event.event_type})

    async def _consume_tool_calls(self) -> None:
        async for event in self._tool_call_subscription:
            try:
                if not isinstance(event, ToolCallEvent) or event.channel != "web" or event.turn_id is None:
                    continue
                self._broadcast(
                    {
                        "kind": "tool",
                        "turn_id": event.turn_id,
                        "call_id": event.call_id,
                        "tool_name": event.tool_name,
                        "phase": event.phase,
                    }
                )
            except Exception:
                self._logger.exception("web tool call event failed", extra={"event_type": event.event_type})

    def _broadcast(self, event: ChatEvent) -> None:
        for queue in tuple(self._subscribers):
            _offer(queue, event)
