from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.types import Message as TelegramMessage

from minibot.adapters.config.schema import FileStorageToolConfig, TelegramChannelConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.messaging.telegram.authorization import is_authorized
from minibot.adapters.messaging.telegram.incoming_media_collector import TelegramIncomingMediaCollector
from minibot.adapters.messaging.telegram.outbound_sender import TelegramOutboundSender
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent, OutboundEvent, OutboundFileEvent


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
        self._outgoing_subscription = event_bus.subscribe()

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

        channel_message = ChannelMessage(
            channel="telegram",
            user_id=message.from_user.id if message.from_user else None,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            attachments=[],
            metadata={
                "username": getattr(message.from_user, "username", None),
                "incoming_files": [entry.model_dump() for entry in incoming_files],
                "incoming_media_errors": incoming_errors,
            },
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
            if isinstance(event, OutboundEvent) and event.response.channel == "telegram":
                await self._outbound_sender.send_text_response(event.response)
            if isinstance(event, OutboundFileEvent) and event.response.channel == "telegram":
                await self._outbound_sender.send_file_response(event)

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

        await self._bot.session.close()
