from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.types import Message as TelegramMessage

from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent, OutboundEvent
from minibot.adapters.config.schema import TelegramChannelConfig


class TelegramService:
    def __init__(self, config: TelegramChannelConfig, event_bus: EventBus) -> None:
        self._config = config
        self._event_bus = event_bus
        self._logger = logging.getLogger("minibot.telegram")
        self._bot = Bot(token=config.bot_token)
        self._dp = Dispatcher()
        self._poll_task: Optional[asyncio.Task[None]] = None
        self._outgoing_task: Optional[asyncio.Task[None]] = None
        self._outgoing_subscription = event_bus.subscribe()

        self._dp.message.register(self._handle_message)

    async def start(self) -> None:
        self._logger.info("starting telegram polling")
        self._poll_task = asyncio.create_task(
            self._dp.start_polling(self._bot, handle_signals=False)
        )
        self._outgoing_task = asyncio.create_task(self._publish_outgoing())

    async def _handle_message(self, message: TelegramMessage) -> None:
        if not self._is_authorized(message):
            user_id = message.from_user.id if message.from_user else None
            chat_id = message.chat.id
            self._logger.warning(
                "blocked unauthorized sender",
                extra={"chat_id": chat_id, "user_id": user_id},
            )
            await self._bot.send_message(
                chat_id=chat_id,
                text=(
                    "User not recognized. Access denied. "
                    f"chat_id={chat_id} user_id={user_id}"
                ),
            )
            return

        channel_message = ChannelMessage(
            channel="telegram",
            user_id=message.from_user.id if message.from_user else None,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=message.text or message.caption or "",
            metadata={"username": getattr(message.from_user, "username", None)},
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
                self._logger.info("sending response", extra={"chat_id": event.response.chat_id})
                await self._bot.send_message(chat_id=event.response.chat_id, text=event.response.text)

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

    def _is_authorized(self, message: TelegramMessage) -> bool:
        allowed_chats = self._config.allowed_chat_ids
        allowed_users = self._config.allowed_user_ids

        chat_allowed = True if not allowed_chats else message.chat.id in allowed_chats
        user_allowed = True
        if allowed_users:
            if message.from_user:
                user_allowed = message.from_user.id in allowed_users
            else:
                user_allowed = False

        if self._config.require_authorized:
            # require both at least one list populated and match
            chat_check = chat_allowed and bool(allowed_chats)
            user_check = user_allowed and bool(allowed_users)
            # if both lists provided require both matches, else require whichever list exists
            if allowed_chats and allowed_users:
                return chat_check and user_check
            if allowed_chats:
                return chat_check
            if allowed_users:
                return user_check
            return False

        return chat_allowed and user_allowed
