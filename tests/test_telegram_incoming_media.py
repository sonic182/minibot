from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

import pytest

from minibot.adapters.config.schema import FileStorageToolConfig, TelegramChannelConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.messaging.telegram.incoming_media_collector import TelegramIncomingMediaCollector


@dataclass
class _Chat:
    id: int


@dataclass
class _User:
    id: int


@dataclass
class _Photo:
    file_unique_id: str


@dataclass
class _Document:
    file_unique_id: str
    file_name: str | None = None
    mime_type: str | None = None


@dataclass
class _Audio:
    file_unique_id: str
    duration: int = 0
    file_name: str | None = None
    mime_type: str | None = None


@dataclass
class _Voice:
    file_unique_id: str
    duration: int = 0
    mime_type: str | None = None


@dataclass
class _MediaMessage:
    chat: _Chat
    from_user: _User | None
    message_id: int
    caption: str | None = None
    photo: list[_Photo] | None = None
    document: _Document | None = None
    audio: _Audio | None = None
    voice: _Voice | None = None


class _BotStub:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def download(self, media: Any, destination: Any) -> None:
        _ = media
        destination.write(self._payload)


def _collector(tmp_path: Path, *, payload: bytes = b"abc") -> TelegramIncomingMediaCollector:
    config = TelegramChannelConfig(bot_token="token")
    file_storage_config = FileStorageToolConfig(
        enabled=True,
        root_dir=str(tmp_path),
        incoming_temp_subdir="uploads/temp",
    )
    return TelegramIncomingMediaCollector(
        bot=_BotStub(payload),
        config=config,
        file_storage_config=file_storage_config,
        local_storage=LocalFileStorage(
            root_dir=str(tmp_path),
            max_write_bytes=file_storage_config.max_write_bytes,
        ),
        managed_root_dir=Path(str(tmp_path)).resolve(),
        logger=logging.getLogger("test.telegram.incoming"),
    )


@pytest.mark.asyncio
async def test_collect_incoming_files_saves_photo_to_temp_dir(tmp_path: Path) -> None:
    collector = _collector(tmp_path, payload=b"abc")
    message = _MediaMessage(chat=_Chat(1), from_user=_User(2), message_id=7, caption="caption", photo=[_Photo("p1")])

    incoming_files, errors = await collector.collect(message)  # type: ignore[arg-type]

    assert not errors
    assert len(incoming_files) == 1
    assert incoming_files[0].path.startswith("uploads/temp/photo_")
    assert incoming_files[0].mime == "image/jpeg"
    assert incoming_files[0].caption == "caption"


@pytest.mark.asyncio
async def test_collect_incoming_files_saves_document_to_temp_dir(tmp_path: Path) -> None:
    collector = _collector(tmp_path, payload=b"pdf-bytes")
    message = _MediaMessage(
        chat=_Chat(1),
        from_user=_User(2),
        message_id=8,
        document=_Document(file_unique_id="d1", file_name="report.pdf", mime_type="application/pdf"),
    )

    incoming_files, errors = await collector.collect(message)  # type: ignore[arg-type]

    assert not errors
    assert len(incoming_files) == 1
    assert incoming_files[0].path == "uploads/temp/report.pdf"
    assert incoming_files[0].filename == "report.pdf"
    assert incoming_files[0].mime == "application/pdf"


@pytest.mark.asyncio
async def test_collect_incoming_files_rejects_document_mime_not_allowed(tmp_path: Path) -> None:
    collector = _collector(tmp_path, payload=b"img-bytes")
    collector._config.allowed_document_mime_types = ["application/pdf"]  # type: ignore[attr-defined]
    message = _MediaMessage(
        chat=_Chat(1),
        from_user=_User(2),
        message_id=9,
        document=_Document(file_unique_id="d2", file_name="photo.jpg", mime_type="image/jpeg"),
    )

    incoming_files, errors = await collector.collect(message)  # type: ignore[arg-type]

    assert not incoming_files
    assert errors == ["document_mime_not_allowed"]


@pytest.mark.asyncio
async def test_collect_incoming_files_saves_audio_to_temp_dir(tmp_path: Path) -> None:
    collector = _collector(tmp_path, payload=b"audio-bytes")
    message = _MediaMessage(
        chat=_Chat(1),
        from_user=_User(2),
        message_id=10,
        caption="audio-caption",
        audio=_Audio(file_unique_id="a1", file_name="sample.mp3", mime_type="audio/mpeg"),
    )

    incoming_files, errors = await collector.collect(message)  # type: ignore[arg-type]

    assert not errors
    assert len(incoming_files) == 1
    assert incoming_files[0].path == "uploads/temp/sample.mp3"
    assert incoming_files[0].filename == "sample.mp3"
    assert incoming_files[0].mime == "audio/mpeg"
    assert incoming_files[0].source == "audio"
    assert incoming_files[0].caption == "audio-caption"
    assert incoming_files[0].duration_seconds == 0


@pytest.mark.asyncio
async def test_collect_incoming_files_saves_voice_to_temp_dir(tmp_path: Path) -> None:
    collector = _collector(tmp_path, payload=b"voice-bytes")
    message = _MediaMessage(
        chat=_Chat(1),
        from_user=_User(2),
        message_id=11,
        voice=_Voice(file_unique_id="v1", mime_type="audio/ogg"),
    )

    incoming_files, errors = await collector.collect(message)  # type: ignore[arg-type]

    assert not errors
    assert len(incoming_files) == 1
    assert incoming_files[0].path.startswith("uploads/temp/voice_")
    assert incoming_files[0].filename.endswith(".ogg")
    assert incoming_files[0].mime == "audio/ogg"
    assert incoming_files[0].source == "voice"
    assert incoming_files[0].duration_seconds == 0


@pytest.mark.asyncio
async def test_collect_incoming_files_rejects_audio_mime_not_allowed(tmp_path: Path) -> None:
    collector = _collector(tmp_path, payload=b"audio-bytes")
    collector._config.allowed_document_mime_types = ["application/pdf"]  # type: ignore[attr-defined]
    message = _MediaMessage(
        chat=_Chat(1),
        from_user=_User(2),
        message_id=12,
        audio=_Audio(file_unique_id="a2", file_name="sample.mp3", mime_type="audio/mpeg"),
    )

    incoming_files, errors = await collector.collect(message)  # type: ignore[arg-type]

    assert not incoming_files
    assert errors == ["audio_mime_not_allowed"]


@pytest.mark.asyncio
async def test_collect_incoming_files_keeps_unique_name_on_collision(tmp_path: Path) -> None:
    collector = _collector(tmp_path, payload=b"pdf-bytes")
    target = tmp_path / "uploads" / "temp" / "report.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"existing")
    message = _MediaMessage(
        chat=_Chat(3),
        from_user=_User(2),
        message_id=8,
        document=_Document(file_unique_id="d1", file_name="report.pdf", mime_type="application/pdf"),
    )

    incoming_files, errors = await collector.collect(message)  # type: ignore[arg-type]

    assert not errors
    assert len(incoming_files) == 1
    assert incoming_files[0].filename != "report.pdf"
    assert incoming_files[0].path.startswith("uploads/temp/document_")
