from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

from minibot.adapters.container import AppContainer
from minibot.app.event_bus import EventBus
from minibot.app.handlers import LLMMessageHandler
from minibot.core.events import MessageEvent, OutboundEvent
from minibot.llm.tools.factory import build_enabled_tools


class Dispatcher:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._subscription = event_bus.subscribe()
        settings = AppContainer.get_settings()
        tools = build_enabled_tools(settings, AppContainer.get_kv_memory_backend())
        self._handler = LLMMessageHandler(
            memory=AppContainer.get_memory_backend(),
            llm_client=AppContainer.get_llm_client(),
            tools=tools,
            default_owner_id=settings.tools.kv_memory.default_owner_id,
        )
        self._logger = logging.getLogger("minibot.dispatcher")
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        async for event in self._subscription:
            if isinstance(event, MessageEvent):
                self._logger.info("processing message event", extra={"event_id": event.event_id})
                await self._handle_message(event)

    async def _handle_message(self, event: MessageEvent) -> None:
        try:
            response = await self._handler.handle(event)
            await self._event_bus.publish(OutboundEvent(response=response))
        except Exception as exc:
            self._logger.exception("failed to handle message", exc_info=exc)

    async def stop(self) -> None:
        await self._subscription.close()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
