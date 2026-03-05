from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import pytest

from minibot.adapters.config.schema import TelegramChannelConfig
from minibot.adapters.messaging.telegram.service import TelegramService
from minibot.core.channels import IncomingFileRef
from minibot.core.events import MessageEvent


@dataclass
class _User:
    id: int
    username: str | None = None


@dataclass
class _Chat:
    id: int


@dataclass
class _Message:
    chat: _Chat
    from_user: _User | None
    message_id: int
    text: str | None = None
    caption: str | None = None


class _BotStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _EventBusStub:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


class _CollectorStub:
    def __init__(self, *, files: list[IncomingFileRef] | None = None, errors: list[str] | None = None) -> None:
        self.files = files or []
        self.errors = errors or []
        self.calls: list[Any] = []

    async def collect(self, message: Any) -> tuple[list[IncomingFileRef], list[str]]:
        self.calls.append(message)
        return self.files, self.errors


def _service(config: TelegramChannelConfig) -> tuple[TelegramService, _BotStub, _EventBusStub, _CollectorStub]:
    service = TelegramService.__new__(TelegramService)
    bot = _BotStub()
    event_bus = _EventBusStub()
    collector = _CollectorStub(
        files=[
            IncomingFileRef(
                path="uploads/temp/a.txt",
                filename="a.txt",
                mime="text/plain",
                size_bytes=1,
                source="document",
            )
        ],
        errors=[],
    )
    service._config = config
    service._bot = bot
    service._event_bus = event_bus
    service._incoming_media_collector = collector
    service._outbound_sender = None
    service._logger = logging.getLogger("test.telegram.service")
    return service, bot, event_bus, collector


@pytest.mark.asyncio
async def test_handle_message_publishes_message_event_when_authorized() -> None:
    config = TelegramChannelConfig(bot_token="token", require_authorized=False)
    service, _, event_bus, collector = _service(config)
    message = _Message(chat=_Chat(1), from_user=_User(2, username="alice"), message_id=7, text="hello")

    await service._handle_message(message)  # type: ignore[arg-type]

    assert len(collector.calls) == 1
    assert len(event_bus.events) == 1
    assert isinstance(event_bus.events[0], MessageEvent)
    published = event_bus.events[0].message
    assert published.text == "hello"
    assert published.metadata["username"] == "alice"
    assert len(published.metadata["incoming_files"]) == 1
    assert published.metadata["incoming_files"][0]["path"] == "uploads/temp/a.txt"


@pytest.mark.asyncio
async def test_handle_message_sends_denied_response_when_unauthorized() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_user_ids=[10],
        require_authorized=False,
    )
    service, bot, event_bus, collector = _service(config)
    message = _Message(chat=_Chat(1), from_user=_User(2), message_id=7, text="hello")

    await service._handle_message(message)  # type: ignore[arg-type]

    assert not collector.calls
    assert not event_bus.events
    assert len(bot.calls) == 1
    assert "Access denied" in bot.calls[0]["text"]
