from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
import logging

import pytest

from minibot.adapters.messaging.telegram import service as telegram_service_module
from minibot.adapters.config.schema import FileStorageToolConfig, TelegramChannelConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.core.channels import ChannelFileResponse, ChannelResponse, RenderableResponse
from minibot.core.events import OutboundFileEvent
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


def _service(config: TelegramChannelConfig) -> TelegramService:
    service = TelegramService.__new__(TelegramService)
    service._config = config
    service._file_storage_config = FileStorageToolConfig()
    service._managed_root_dir = Path(service._file_storage_config.root_dir).resolve()
    service._local_storage = LocalFileStorage(
        root_dir=service._file_storage_config.root_dir,
        max_write_bytes=service._file_storage_config.max_write_bytes,
    )
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
async def test_collect_incoming_files_saves_photo_to_temp_dir(tmp_path: Path) -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)
    service._file_storage_config = FileStorageToolConfig(
        enabled=True,
        root_dir=str(tmp_path),
        incoming_temp_subdir="uploads/temp",
    )
    service._managed_root_dir = Path(str(tmp_path)).resolve()
    service._local_storage = LocalFileStorage(
        root_dir=str(tmp_path),
        max_write_bytes=service._file_storage_config.max_write_bytes,
    )

    async def _download(_media):
        return b"abc"

    service._download_media_bytes = _download  # type: ignore[attr-defined]
    message = _MediaMessage(chat=_Chat(1), from_user=_User(2), photo=[_Photo(file_unique_id="p1")])
    message.message_id = 7  # type: ignore[attr-defined]
    message.caption = "caption"  # type: ignore[attr-defined]

    incoming_files, errors = await service._collect_incoming_files(message)  # type: ignore[arg-type]

    assert not errors
    assert len(incoming_files) == 1
    assert incoming_files[0].path.startswith("uploads/temp/photo_")
    assert incoming_files[0].mime == "image/jpeg"
    assert incoming_files[0].caption == "caption"


@pytest.mark.asyncio
async def test_collect_incoming_files_saves_document_to_temp_dir(tmp_path: Path) -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)
    service._file_storage_config = FileStorageToolConfig(
        enabled=True,
        root_dir=str(tmp_path),
        incoming_temp_subdir="uploads/temp",
    )
    service._managed_root_dir = Path(str(tmp_path)).resolve()
    service._local_storage = LocalFileStorage(
        root_dir=str(tmp_path),
        max_write_bytes=service._file_storage_config.max_write_bytes,
    )

    async def _download(_media):
        return b"pdf-bytes"

    service._download_media_bytes = _download  # type: ignore[attr-defined]
    message = _MediaMessage(
        chat=_Chat(1),
        from_user=_User(2),
        document=_Document(file_unique_id="d1", file_name="report.pdf", mime_type="application/pdf"),
    )
    message.message_id = 8  # type: ignore[attr-defined]

    incoming_files, errors = await service._collect_incoming_files(message)  # type: ignore[arg-type]

    assert not errors
    assert len(incoming_files) == 1
    assert incoming_files[0].path == "uploads/temp/report.pdf"
    assert incoming_files[0].filename == "report.pdf"
    assert incoming_files[0].mime == "application/pdf"


@pytest.mark.asyncio
async def test_collect_incoming_files_rejects_document_mime_not_allowed(tmp_path: Path) -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)
    service._file_storage_config = FileStorageToolConfig(
        enabled=True,
        root_dir=str(tmp_path),
        incoming_temp_subdir="uploads/temp",
    )
    service._managed_root_dir = Path(str(tmp_path)).resolve()
    service._local_storage = LocalFileStorage(
        root_dir=str(tmp_path),
        max_write_bytes=service._file_storage_config.max_write_bytes,
    )
    service._config.allowed_document_mime_types = ["application/pdf"]

    async def _download(_media):
        return b"img-bytes"

    service._download_media_bytes = _download  # type: ignore[attr-defined]
    message = _MediaMessage(
        chat=_Chat(1),
        from_user=_User(2),
        document=_Document(file_unique_id="d2", file_name="photo.jpg", mime_type="image/jpeg"),
    )
    message.message_id = 9  # type: ignore[attr-defined]

    incoming_files, errors = await service._collect_incoming_files(message)  # type: ignore[arg-type]

    assert not incoming_files
    assert errors == ["document_mime_not_allowed"]


@pytest.mark.asyncio
async def test_send_file_response_uses_send_document(tmp_path: Path) -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)
    target = tmp_path / "report.txt"
    target.write_text("hello", encoding="utf-8")

    class _BotStub:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def send_document(self, chat_id: int, document: Any, caption: str | None = None) -> None:
            self.calls.append({"chat_id": chat_id, "document": document, "caption": caption})

    bot = _BotStub()
    service._bot = bot  # type: ignore[attr-defined]

    event = OutboundFileEvent(
        response=ChannelFileResponse(channel="telegram", chat_id=1, file_path=str(target), caption="latest")
    )
    await service._send_file_response(event)

    assert len(bot.calls) == 1
    assert bot.calls[0]["chat_id"] == 1
    assert bot.calls[0]["caption"] == "latest"


@pytest.mark.asyncio
async def test_collect_incoming_files_keeps_unique_name_on_collision(tmp_path: Path) -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)
    service._file_storage_config = FileStorageToolConfig(
        enabled=True,
        root_dir=str(tmp_path),
        incoming_temp_subdir="uploads/temp",
    )
    service._managed_root_dir = Path(str(tmp_path)).resolve()
    service._local_storage = LocalFileStorage(
        root_dir=str(tmp_path),
        max_write_bytes=service._file_storage_config.max_write_bytes,
    )
    target = tmp_path / "uploads" / "temp" / "report.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"existing")

    async def _download(_media):
        return b"pdf-bytes"

    service._download_media_bytes = _download  # type: ignore[attr-defined]
    message = _MediaMessage(
        chat=_Chat(3),
        from_user=_User(2),
        document=_Document(file_unique_id="d1", file_name="report.pdf", mime_type="application/pdf"),
    )
    message.message_id = 8  # type: ignore[attr-defined]

    incoming_files, errors = await service._collect_incoming_files(message)  # type: ignore[arg-type]

    assert not errors
    assert len(incoming_files) == 1
    assert incoming_files[0].filename != "report.pdf"
    assert incoming_files[0].path.startswith("uploads/temp/document_")


@pytest.mark.asyncio
async def test_send_parse_mode_chunks_sets_markdown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)

    def _markdownify(value: str) -> str:
        return f"converted:{value}"

    monkeypatch.setattr(telegram_service_module, "telegram_markdownify", _markdownify)

    class _BotStub:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def send_message(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    bot = _BotStub()
    service._bot = bot  # type: ignore[attr-defined]

    success, parse_error = await service._send_parse_mode_chunks(
        chat_id=1,
        render=RenderableResponse(kind="markdown_v2", text="*bold*"),
    )

    assert success is True
    assert parse_error is None
    assert len(bot.calls) == 1
    assert bot.calls[0]["text"] == "converted:*bold*"
    assert bot.calls[0]["parse_mode"].value == "MarkdownV2"


@pytest.mark.asyncio
async def test_send_parse_mode_chunks_falls_back_to_plain_text_when_markdownify_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)

    def _markdownify(_value: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(telegram_service_module, "telegram_markdownify", _markdownify)

    class _BotStub:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def send_message(self, **kwargs: Any) -> None:
            self.calls.append(kwargs)

    bot = _BotStub()
    service._bot = bot  # type: ignore[attr-defined]

    success, parse_error = await service._send_parse_mode_chunks(
        chat_id=1,
        render=RenderableResponse(kind="markdown_v2", text="*bold*"),
    )

    assert success is True
    assert parse_error is None
    assert len(bot.calls) == 1
    assert bot.calls[0]["text"] == "*bold*"
    assert bot.calls[0]["parse_mode"] is None


@pytest.mark.asyncio
async def test_send_text_response_falls_back_to_plain_on_render_failure() -> None:
    config = TelegramChannelConfig(bot_token="token")
    service = _service(config)
    response = ChannelResponse(
        channel="telegram",
        chat_id=1,
        text="<b>hello</b>",
        render=RenderableResponse(kind="html", text="<b>hello</b>"),
    )

    calls: list[str] = []

    async def _send_render_chunks(chat_id: int, render: RenderableResponse) -> tuple[bool, str | None]:
        _ = chat_id
        calls.append(render.kind)
        return (render.kind == "text", None)

    service._send_render_chunks = _send_render_chunks  # type: ignore[attr-defined]

    await service._send_text_response(response)

    assert calls == ["html", "text"]
