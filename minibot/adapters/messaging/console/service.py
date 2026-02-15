from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from html import unescape
import logging
import re
from typing import Optional

from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage, ChannelResponse, RenderableResponse
from minibot.core.events import MessageEvent, OutboundEvent
from minibot.shared.console_compat import CompatConsole, format_assistant_output


_TAG_RE = re.compile(r"<[^>]+>")


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
        console: CompatConsole | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._chat_id = chat_id
        self._user_id = user_id
        self._console = console or CompatConsole()
        self._logger = logging.getLogger("minibot.console")
        self._message_id = 0
        self._subscription = event_bus.subscribe()
        self._outgoing_task: Optional[asyncio.Task[None]] = None
        self._responses: asyncio.Queue[ConsoleResponse] = asyncio.Queue()

    async def start(self) -> None:
        self._outgoing_task = asyncio.create_task(self._consume_outgoing())

    async def stop(self) -> None:
        await self._subscription.close()
        if self._outgoing_task is not None:
            self._outgoing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._outgoing_task

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
        if render.kind == "markdown_v2":
            text = render.text
            self._console.print(format_assistant_output("markdown_v2", text))
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
