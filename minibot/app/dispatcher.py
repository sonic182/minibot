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
        prompt_service = AppContainer.get_scheduled_prompt_service()
        memory_backend = AppContainer.get_memory_backend()
        tools = build_enabled_tools(
            settings,
            memory_backend,
            AppContainer.get_kv_memory_backend(),
            prompt_service,
            event_bus=self._event_bus,
            file_storage=AppContainer.get_file_storage(),
        )
        self._handler = LLMMessageHandler(
            memory=memory_backend,
            llm_client=AppContainer.get_llm_client(),
            tools=tools,
            default_owner_id=settings.tools.kv_memory.default_owner_id,
            max_history_messages=settings.memory.max_history_messages,
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
            message = event.message
            self._logger.debug(
                "incoming message",
                extra={
                    "event_id": event.event_id,
                    "chat_id": message.chat_id,
                    "user_id": message.user_id,
                    "text": message.text,
                },
            )
            response = await self._handler.handle(event)
            should_reply = response.metadata.get("should_reply", True)
            self._logger.debug(
                "handler response",
                extra={
                    "event_id": event.event_id,
                    "chat_id": response.chat_id,
                    "text": response.text,
                    "should_reply": should_reply,
                    "llm_provider": response.metadata.get("llm_provider"),
                    "llm_model": response.metadata.get("llm_model"),
                },
            )
            if not should_reply:
                self._logger.info("skipping user reply as instructed", extra={"event_id": event.event_id})
                return
            await self._event_bus.publish(OutboundEvent(response=response))
        except Exception as exc:
            self._logger.exception("failed to handle message", exc_info=exc)

    async def stop(self) -> None:
        await self._subscription.close()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
