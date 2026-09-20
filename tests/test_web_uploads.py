from __future__ import annotations

import logging

import pytest

from minibot.adapters.config.schema import AudioTranscriptionToolConfig, FileStorageToolConfig, HTTPServerConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.http.uploads import UploadError, WebUploadManager


def _manager(tmp_path, **http_overrides: object) -> WebUploadManager:
    storage = LocalFileStorage(str(tmp_path), max_write_bytes=64_000)
    return WebUploadManager(
        storage=storage,
        http_config=HTTPServerConfig(enabled=True, **http_overrides),
        file_storage_config=FileStorageToolConfig(enabled=True),
        audio_config=AudioTranscriptionToolConfig(enabled=True),
        supports_media_inputs=True,
        logger=logging.getLogger("test.web_uploads"),
    )


@pytest.mark.asyncio
async def test_web_image_upload_builds_safe_model_attachment(tmp_path) -> None:
    manager = _manager(tmp_path)
    session = manager.new_session()
    await session.start(
        {
            "upload_id": "image-1",
            "filename": "photo.png",
            "mime": "image/png",
            "size_bytes": 12,
            "media_kind": "image",
        }
    )
    await session.append(b"\x89PNG\r\n\x1a\nbody")
    completed = await session.complete("image-1")

    attachments, incoming_files, display = await session.message_parts(["image-1"])

    assert completed.path.startswith("uploads/temp/web/")
    assert attachments[0]["type"] == "input_image"
    assert attachments[0]["image_url"].startswith("data:image/png;base64,")
    assert incoming_files[0]["source"] == "photo"
    assert display[0]["id"] == "image-1"


@pytest.mark.asyncio
async def test_web_upload_normalizes_parameterized_media_type(tmp_path) -> None:
    manager = _manager(tmp_path)
    session = manager.new_session()
    prepared = await session.start(
        {
            "upload_id": "audio-1",
            "filename": "recording.webm",
            "mime": "audio/webm;codecs=opus",
            "size_bytes": 4,
            "media_kind": "audio",
        }
    )
    assert prepared.mime == "audio/webm"
    await session.close()


@pytest.mark.asyncio
async def test_web_upload_rejects_total_limit_and_removes_incomplete_file(tmp_path) -> None:
    manager = _manager(tmp_path, chat_upload_max_total_bytes=10)
    session = manager.new_session()
    await session.start(
        {
            "upload_id": "image-1",
            "filename": "photo.png",
            "mime": "image/png",
            "size_bytes": 10,
            "media_kind": "image",
        }
    )
    with pytest.raises(UploadError, match="does not match"):
        await session.complete("image-1")
    with pytest.raises(UploadError, match="total size"):
        await session.start(
            {
                "upload_id": "image-2",
                "filename": "photo.png",
                "mime": "image/png",
                "size_bytes": 11,
                "media_kind": "image",
            }
        )
    await session.close()


@pytest.mark.asyncio
async def test_web_upload_rejects_invalid_image_signature(tmp_path) -> None:
    manager = _manager(tmp_path)
    session = manager.new_session()
    await session.start(
        {
            "upload_id": "image-1",
            "filename": "photo.png",
            "mime": "image/png",
            "size_bytes": 8,
            "media_kind": "image",
        }
    )
    await session.append(b"not-png!")
    with pytest.raises(UploadError, match="does not match"):
        await session.complete("image-1")
