from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass
from html import unescape
from typing import Any, Protocol

from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage, ChannelResponse, RenderableResponse
from minibot.core.events import MessageEvent, OutboundEvent, ReasoningEvent, ToolCallEvent
from minibot.shared.console_compat import CompatConsole, format_assistant_output

_TAG_RE = re.compile(r"<[^>]+>")


_LIVE_QUEUE_LIMIT = 256


class ConsoleSink(Protocol):
    def print(self, value: object) -> None:
        """Render a value; TUI sinks may discard it."""


def _offer(queue: asyncio.Queue[Any], item: Any) -> None:
    """Enqueue for a live renderer, dropping the oldest entry when nobody is keeping up.

    The plain console never drains these, and tool arguments can hold credentials, so an unbounded
    buffer would both grow for the life of the session and keep those values in memory.
    """
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
            queue.task_done()
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(item)


@dataclass(frozen=True)
class ConsoleResponse:
    response: ChannelResponse
    rendered_text: str


class ConsoleService:
    def __init__(
        self,
        event_bus: EventBus,
        *,
        chat_id: int = 1,
        user_id: int = 1,
        console: ConsoleSink | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._chat_id = chat_id
        self._user_id = user_id
        self._console = console or CompatConsole()
        self._logger = logging.getLogger("minibot.console")
        self._message_id = 0
        self._subscription = event_bus.subscribe(types=(OutboundEvent,))
        self._reasoning_subscription = event_bus.subscribe(types=(ReasoningEvent,))
        # Lossy: a tool lifecycle is the bus's high-volume case and this feed only draws on screen,
        # so dropping a notice under pressure beats applying back-pressure to the running turn.
        self._tool_call_subscription = event_bus.subscribe(types=(ToolCallEvent,), lossy=True)
        self._outgoing_task: asyncio.Task[None] | None = None
        self._reasoning_task: asyncio.Task[None] | None = None
        self._tool_call_task: asyncio.Task[None] | None = None
        self._responses: asyncio.Queue[ConsoleResponse] = asyncio.Queue()
        self._reasoning: asyncio.Queue[str] = asyncio.Queue(maxsize=_LIVE_QUEUE_LIMIT)
        self._tool_calls: asyncio.Queue[str] = asyncio.Queue(maxsize=_LIVE_QUEUE_LIMIT)

    async def start(self) -> None:
        self._outgoing_task = asyncio.create_task(self._consume_outgoing())
        self._reasoning_task = asyncio.create_task(self._consume_reasoning())
        self._tool_call_task = asyncio.create_task(self._consume_tool_calls())

    async def stop(self) -> None:
        await self._subscription.close()
        await self._reasoning_subscription.close()
        await self._tool_call_subscription.close()
        for task in (self._outgoing_task, self._reasoning_task, self._tool_call_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def publish_user_message(self, text: str) -> None:
        self._message_id += 1
        message = ChannelMessage(
            channel="console",
            user_id=self._user_id,
            chat_id=self._chat_id,
            message_id=self._message_id,
            text=text,
            attachments=[],
            metadata={},
        )
        await self._event_bus.publish(MessageEvent(message=message))

    async def wait_for_response(self, timeout_seconds: float) -> ConsoleResponse:
        result = await asyncio.wait_for(self._responses.get(), timeout=timeout_seconds)
        self._responses.task_done()
        return result

    async def next_reasoning(self) -> str:
        """Await the next reasoning chunk of the running turn, for channels that render it live."""
        text = await self._reasoning.get()
        self._reasoning.task_done()
        return text

    def drain_reasoning(self) -> None:
        while not self._reasoning.empty():
            self._reasoning.get_nowait()
            self._reasoning.task_done()

    async def next_tool_call(self) -> str:
        """Await the next tool-call notice of the running turn, for channels that render them live."""
        call = await self._tool_calls.get()
        self._tool_calls.task_done()
        return call

    def drain_tool_calls(self) -> None:
        while not self._tool_calls.empty():
            self._tool_calls.get_nowait()
            self._tool_calls.task_done()

    async def _consume_tool_calls(self) -> None:
        async for event in self._tool_call_subscription:
            if not isinstance(event, ToolCallEvent):
                continue
            if event.channel is not None and event.channel != "console":
                continue
            # Success needs no line of its own: the answer is the evidence the call worked.
            if event.phase == "completed":
                continue
            _offer(self._tool_calls, event.detail)

    async def _consume_reasoning(self) -> None:
        async for event in self._reasoning_subscription:
            if not isinstance(event, ReasoningEvent):
                continue
            if event.channel is not None and event.channel != "console":
                continue
            if event.text.strip():
                _offer(self._reasoning, event.text)

    async def _consume_outgoing(self) -> None:
        async for event in self._subscription:
            if not isinstance(event, OutboundEvent):
                continue
            response = event.response
            if response.channel != "console":
                continue
            rendered = self._render_response(response)
            self._responses.put_nowait(ConsoleResponse(response=response, rendered_text=rendered))

    def _render_response(self, response: ChannelResponse) -> str:
        render = response.render or RenderableResponse(kind="text", text=response.text)
        if render.kind == "markdown":
            text = render.text
            self._console.print(format_assistant_output("markdown", text))
            return text
        if render.kind == "html":
            text = _render_html_to_text(render.text)
            self._console.print(format_assistant_output("html", text))
            return text
        text = render.text
        self._console.print(format_assistant_output("text", text))
        return text


def _render_html_to_text(text: str) -> str:
    return unescape(_TAG_RE.sub("", text or ""))
