from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatAction
from aiogram.types import Message as TelegramMessage

from minibot.adapters.config.schema import FileStorageToolConfig, TelegramChannelConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.messaging.telegram.authorization import is_authorized
from minibot.adapters.messaging.telegram.incoming_media_collector import TelegramIncomingMediaCollector
from minibot.adapters.messaging.telegram.outbound_sender import TelegramOutboundSender
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import (
    MessageEvent,
    OutboundEvent,
    OutboundFileEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)

_TYPING_INTERVAL_SECONDS = 4


class TelegramService:
    def __init__(
        self,
        config: TelegramChannelConfig,
        event_bus: EventBus,
        file_storage_config: FileStorageToolConfig | None = None,
    ) -> None:
        self._config = config
        self._file_storage_config = file_storage_config or FileStorageToolConfig()
        self._managed_root_dir = Path(self._file_storage_config.root_dir).resolve()
        self._local_storage = LocalFileStorage(
            root_dir=self._file_storage_config.root_dir,
            max_write_bytes=self._file_storage_config.max_write_bytes,
            allow_outside_root=self._file_storage_config.allow_outside_root,
        )
        self._event_bus = event_bus
        self._logger = logging.getLogger("minibot.telegram")
        self._bot = Bot(token=config.bot_token)
        self._dp = Dispatcher()
        self._incoming_media_collector = TelegramIncomingMediaCollector(
            bot=self._bot,
            config=self._config,
            file_storage_config=self._file_storage_config,
            local_storage=self._local_storage,
            managed_root_dir=self._managed_root_dir,
            logger=self._logger,
        )
        self._outbound_sender = TelegramOutboundSender(
            bot=self._bot,
            event_bus=self._event_bus,
            config=self._config,
            logger=self._logger,
        )
        self._poll_task: asyncio.Task[None] | None = None
        self._outgoing_task: asyncio.Task[None] | None = None
        self._typing_tasks: dict[str, asyncio.Task[None]] = {}
        self._outgoing_subscription = event_bus.subscribe(
            types=(OutboundEvent, OutboundFileEvent, TurnStartedEvent, TurnCompletedEvent, TurnFailedEvent)
        )

        self._dp.message.register(self._handle_message)

    async def start(self) -> None:
        self._logger.info("starting telegram polling")
        self._poll_task = asyncio.create_task(self._dp.start_polling(self._bot, handle_signals=False))
        self._outgoing_task = asyncio.create_task(self._publish_outgoing())

    async def _handle_message(self, message: TelegramMessage) -> None:
        if not is_authorized(self._config, message):
            user_id = message.from_user.id if message.from_user else None
            chat_id = message.chat.id
            self._logger.warning(
                "blocked unauthorized sender",
                extra={"chat_id": chat_id, "user_id": user_id},
            )
            await self._bot.send_message(
                chat_id=chat_id,
                text=(f"User not recognized. Access denied. chat_id={chat_id} user_id={user_id}"),
            )
            return

        incoming_files, incoming_errors = await self._incoming_media_collector.collect(message)
        if incoming_files:
            self._logger.info(
                "received telegram managed incoming files",
                extra={
                    "chat_id": message.chat.id,
                    "user_id": message.from_user.id if message.from_user else None,
                    "file_count": len(incoming_files),
                },
            )
        if incoming_errors:
            self._logger.warning(
                "telegram incoming media skipped",
                extra={
                    "chat_id": message.chat.id,
                    "user_id": message.from_user.id if message.from_user else None,
                    "errors": incoming_errors,
                },
            )

        text = message.text or message.caption or ""
        if not text and not incoming_files and incoming_errors:
            await self._bot.send_message(chat_id=message.chat.id, text="I could not process the attachment you sent.")
            return

        metadata: dict[str, object] = {
            "username": getattr(message.from_user, "username", None),
            "incoming_files": [entry.model_dump() for entry in incoming_files],
            "incoming_media_errors": incoming_errors,
        }
        reply_metadata = _reply_metadata(message)
        if reply_metadata is not None:
            metadata["reply_to"] = reply_metadata
        channel_message = ChannelMessage(
            channel="telegram",
            user_id=message.from_user.id if message.from_user else None,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            attachments=[],
            metadata=metadata,
        )
        self._logger.info(
            "received message",
            extra={
                "chat_id": message.chat.id,
                "user_id": message.from_user.id if message.from_user else None,
            },
        )
        await self._event_bus.publish(MessageEvent(message=channel_message))

    async def _publish_outgoing(self) -> None:
        async for event in self._outgoing_subscription:
            if isinstance(event, TurnStartedEvent) and event.channel == "telegram" and event.chat_id is not None:
                self._start_typing(event.turn_id, event.chat_id)
            if isinstance(event, (TurnCompletedEvent, TurnFailedEvent)) and event.channel == "telegram":
                self._stop_typing(event.turn_id)
            if isinstance(event, OutboundEvent) and event.response.channel == "telegram":
                await self._outbound_sender.send_text_response(event.response)
            if isinstance(event, OutboundFileEvent) and event.response.channel == "telegram":
                await self._outbound_sender.send_file_response(event)

    def _start_typing(self, turn_id: str, chat_id: int) -> None:
        self._stop_typing(turn_id)
        self._typing_tasks[turn_id] = asyncio.create_task(self._send_typing(turn_id, chat_id))

    def _stop_typing(self, turn_id: str) -> None:
        typing_task = self._typing_tasks.pop(turn_id, None)
        if typing_task is not None:
            typing_task.cancel()

    async def _send_typing(self, turn_id: str, chat_id: int) -> None:
        try:
            while True:
                await self._bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(_TYPING_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.debug("telegram typing indicator failed", extra={"chat_id": chat_id}, exc_info=True)
        finally:
            self._typing_tasks.pop(turn_id, None)

    async def stop(self) -> None:
        if self._poll_task:
            self._logger.info("stopping telegram polling")
            with contextlib.suppress(Exception):
                await self._dp.stop_polling()
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, ValueError):
                await self._poll_task

        if self._outgoing_task:
            await self._outgoing_subscription.close()
            self._outgoing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._outgoing_task

        typing_tasks = list(self._typing_tasks.values())
        self._typing_tasks.clear()
        for typing_task in typing_tasks:
            typing_task.cancel()
        if typing_tasks:
            await asyncio.gather(*typing_tasks, return_exceptions=True)

        await self._bot.session.close()


def _reply_metadata(message: TelegramMessage) -> dict[str, int | str] | None:
    reply_to_message = getattr(message, "reply_to_message", None)
    if reply_to_message is None:
        return None
    metadata: dict[str, int | str] = {"message_id": reply_to_message.message_id}
    reply_text = reply_to_message.text or reply_to_message.caption
    if reply_text:
        metadata["text"] = reply_text
    username = getattr(reply_to_message.from_user, "username", None)
    if username:
        metadata["username"] = username
    quote = getattr(message, "quote", None)
    if quote is not None and quote.text:
        metadata["quote"] = quote.text
    return metadata
