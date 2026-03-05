from __future__ import annotations

from aiogram.types import Message as TelegramMessage

from minibot.adapters.config.schema import TelegramChannelConfig


def is_authorized(config: TelegramChannelConfig, message: TelegramMessage) -> bool:
    allowed_chats = config.allowed_chat_ids
    allowed_users = config.allowed_user_ids

    chat_allowed = True if not allowed_chats else message.chat.id in allowed_chats
    user_allowed = True
    if allowed_users:
        if message.from_user:
            user_allowed = message.from_user.id in allowed_users
        else:
            user_allowed = False

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
