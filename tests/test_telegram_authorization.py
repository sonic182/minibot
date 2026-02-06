from __future__ import annotations

from dataclasses import dataclass

from minibot.adapters.config.schema import TelegramChannelConfig
from minibot.adapters.messaging.telegram.service import TelegramService


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


def _service(config: TelegramChannelConfig) -> TelegramService:
    service = TelegramService.__new__(TelegramService)
    service._config = config
    return service


def test_is_authorized_allows_when_no_whitelist() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[],
        allowed_user_ids=[],
        require_authorized=False,
    )
    service = _service(config)

    assert service._is_authorized(_Message(chat=_Chat(123), from_user=_User(456))) is True


def test_is_authorized_requires_list_match_when_enforced() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[100],
        allowed_user_ids=[200],
        require_authorized=True,
    )
    service = _service(config)

    assert service._is_authorized(_Message(chat=_Chat(100), from_user=_User(200))) is True
    assert service._is_authorized(_Message(chat=_Chat(100), from_user=_User(999))) is False
    assert service._is_authorized(_Message(chat=_Chat(999), from_user=_User(200))) is False


def test_is_authorized_denies_missing_user_when_user_whitelist_set() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[],
        allowed_user_ids=[1],
        require_authorized=False,
    )
    service = _service(config)

    assert service._is_authorized(_Message(chat=_Chat(123), from_user=None)) is False


def test_is_authorized_denies_when_enforced_with_empty_whitelists() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[],
        allowed_user_ids=[],
        require_authorized=True,
    )
    service = _service(config)

    assert service._is_authorized(_Message(chat=_Chat(123), from_user=_User(456))) is False
