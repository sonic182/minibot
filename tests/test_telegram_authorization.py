from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, cast
import logging

import pytest

from minibot.app.event_bus import EventBus
from minibot.adapters.config.schema import TelegramChannelConfig
from minibot.adapters.messaging.telegram.service import TelegramService
from minibot.core.channels import ChannelMediaResponse
from minibot.core.events import OutboundMediaEvent


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


@dataclass
class _Photo:
    file_unique_id: str


@dataclass
class _Document:
    file_unique_id: str
    file_name: str | None = None
    mime_type: str | None = None


@dataclass
class _MediaMessage:
    chat: _Chat
    from_user: _User | None
    photo: list[_Photo] | None = None
    document: _Document | None = None


class _BotStub:
    def __init__(self) -> None:
        self.photos: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []

    async def send_photo(self, *, chat_id: int, photo: Any, caption: str | None = None) -> None:
        self.photos.append({"chat_id": chat_id, "photo": photo, "caption": caption})

    async def send_document(self, *, chat_id: int, document: Any, caption: str | None = None) -> None:
        self.documents.append({"chat_id": chat_id, "document": document, "caption": caption})


def _service(config: TelegramChannelConfig) -> TelegramService:
    service = TelegramService.__new__(TelegramService)
    service._config = config
    service._logger = logging.getLogger("test.telegram")
    return service


def test_is_authorized_allows_when_no_whitelist() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[],
        allowed_user_ids=[],
        require_authorized=False,
    )
    service = _service(config)

    assert service._is_authorized(cast(Any, _Message(chat=_Chat(123), from_user=_User(456)))) is True


def test_is_authorized_requires_list_match_when_enforced() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[100],
        allowed_user_ids=[200],
        require_authorized=True,
    )
    service = _service(config)

    assert service._is_authorized(cast(Any, _Message(chat=_Chat(100), from_user=_User(200)))) is True
    assert service._is_authorized(cast(Any, _Message(chat=_Chat(100), from_user=_User(999)))) is False
    assert service._is_authorized(cast(Any, _Message(chat=_Chat(999), from_user=_User(200)))) is False


def test_is_authorized_denies_missing_user_when_user_whitelist_set() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[],
        allowed_user_ids=[1],
        require_authorized=False,
    )
    service = _service(config)

    assert service._is_authorized(cast(Any, _Message(chat=_Chat(123), from_user=None))) is False


def test_is_authorized_denies_when_enforced_with_empty_whitelists() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_chat_ids=[],
        allowed_user_ids=[],
        require_authorized=True,
    )
    service = _service(config)

    assert service._is_authorized(cast(Any, _Message(chat=_Chat(123), from_user=_User(456)))) is False


def test_chunk_text_splits_long_messages_preserving_limits() -> None:
    text = "line1\n" + ("x" * 4100)
    chunks = TelegramService._chunk_text(text, 4000)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") in text.replace("\n", "")


def test_chunk_text_returns_single_chunk_for_short_message() -> None:
    text = "hola"
    chunks = TelegramService._chunk_text(text, 4000)

    assert chunks == [text]


@pytest.mark.asyncio
async def test_build_attachments_generates_image_part() -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)

    async def _download(_media):
        return b"abc"

    service._download_media_bytes = _download  # type: ignore[attr-defined]
    message = _MediaMessage(chat=_Chat(1), from_user=_User(2), photo=[_Photo(file_unique_id="p1")])

    attachments, errors = await service._build_attachments(message)  # type: ignore[arg-type]

    assert not errors
    assert len(attachments) == 1
    assert attachments[0]["type"] == "input_image"
    assert attachments[0]["image_url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_build_attachments_generates_file_part() -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)

    async def _download(_media):
        return b"pdf-bytes"

    service._download_media_bytes = _download  # type: ignore[attr-defined]
    message = _MediaMessage(
        chat=_Chat(1),
        from_user=_User(2),
        document=_Document(file_unique_id="d1", file_name="report.pdf", mime_type="application/pdf"),
    )

    attachments, errors = await service._build_attachments(message)  # type: ignore[arg-type]

    assert not errors
    assert len(attachments) == 1
    assert attachments[0]["type"] == "input_file"
    assert attachments[0]["filename"] == "report.pdf"
    assert attachments[0]["file_data"].startswith("data:application/pdf;base64,")


@pytest.mark.asyncio
async def test_build_attachments_converts_image_document_to_input_image() -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)

    async def _download(_media):
        return b"img-bytes"

    service._download_media_bytes = _download  # type: ignore[attr-defined]
    message = _MediaMessage(
        chat=_Chat(1),
        from_user=_User(2),
        document=_Document(file_unique_id="d2", file_name="photo.jpg", mime_type="image/jpeg"),
    )

    attachments, errors = await service._build_attachments(message)  # type: ignore[arg-type]

    assert not errors
    assert len(attachments) == 1
    assert attachments[0]["type"] == "input_image"
    assert attachments[0]["image_url"].startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_publish_outgoing_sends_media_events(tmp_path) -> None:
    bus = EventBus()
    service = TelegramService.__new__(TelegramService)
    service._config = TelegramChannelConfig(bot_token="token")
    service._logger = logging.getLogger("test.telegram")
    service._outgoing_subscription = bus.subscribe()
    service._bot = _BotStub()

    outgoing_task = asyncio.create_task(service._publish_outgoing())
    file_path = tmp_path / "report.txt"
    file_path.write_text("hello", encoding="utf-8")

    await bus.publish(
        OutboundMediaEvent(
            response=ChannelMediaResponse(
                channel="telegram",
                chat_id=10,
                media_type="document",
                file_path=str(file_path),
                caption="report",
            )
        )
    )
    await asyncio.sleep(0.05)
    await service._outgoing_subscription.close()
    outgoing_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outgoing_task

    assert len(service._bot.documents) == 1
    assert service._bot.documents[0]["chat_id"] == 10
    assert service._bot.documents[0]["caption"] == "report"
