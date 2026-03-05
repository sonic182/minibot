from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from minibot.adapters.config.schema import TelegramChannelConfig
from minibot.adapters.messaging.telegram.authorization import is_authorized


@dataclass
class _User:
    id: int


@dataclass
class _Chat:
    id: int


@dataclass
class _Message:
    chat: _Chat
    from_user: _User | None


def test_is_authorized_allows_when_no_whitelist() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[],
        allowed_user_ids=[],
        require_authorized=False,
    )

    assert is_authorized(config, cast(Any, _Message(chat=_Chat(123), from_user=_User(456)))) is True


def test_is_authorized_requires_list_match_when_enforced() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[100],
        allowed_user_ids=[200],
        require_authorized=True,
    )

    assert is_authorized(config, cast(Any, _Message(chat=_Chat(100), from_user=_User(200)))) is True
    assert is_authorized(config, cast(Any, _Message(chat=_Chat(100), from_user=_User(999)))) is False
    assert is_authorized(config, cast(Any, _Message(chat=_Chat(999), from_user=_User(200)))) is False


def test_is_authorized_denies_missing_user_when_user_whitelist_set() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[],
        allowed_user_ids=[1],
        require_authorized=False,
    )

    assert is_authorized(config, cast(Any, _Message(chat=_Chat(123), from_user=None))) is False


def test_is_authorized_denies_when_enforced_with_empty_whitelists() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[],
        allowed_user_ids=[],
        require_authorized=True,
    )

    assert is_authorized(config, cast(Any, _Message(chat=_Chat(123), from_user=_User(456)))) is False
