from __future__ import annotations

from aiogram.types import Message as TelegramMessage

from minibot.config.schema import TelegramChannelConfig


def is_authorized(config: TelegramChannelConfig, message: TelegramMessage) -> bool:
    return is_authorized_ids(config, message.chat.id, message.from_user.id if message.from_user else None)


def is_authorized_ids(config: TelegramChannelConfig, chat_id: int, user_id: int | None) -> bool:
    allowed_chats = config.allowed_chat_ids
    allowed_users = config.allowed_user_ids

    chat_allowed = True if not allowed_chats else chat_id in allowed_chats
    user_allowed = True
    if allowed_users:
        user_allowed = user_id is not None and user_id in allowed_users

    if config.require_authorized:
        chat_check = chat_allowed and bool(allowed_chats)
        user_check = user_allowed and bool(allowed_users)
        if allowed_chats and allowed_users:
            return chat_check and user_check
        if allowed_chats:
            return chat_check
        if allowed_users:
            return user_check
        return False

    return chat_allowed and user_allowed
