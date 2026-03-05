from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import Message as TelegramMessage

from minibot.adapters.config.schema import FileStorageToolConfig, TelegramChannelConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.messaging.telegram.incoming_media_mapper import TelegramIncomingMediaMapper
from minibot.core.channels import IncomingFileRef
from minibot.shared.path_utils import to_posix_relative


class TelegramIncomingMediaCollector:
    def __init__(
        self,
        *,
        bot: Bot,
        config: TelegramChannelConfig,
        file_storage_config: FileStorageToolConfig,
        local_storage: LocalFileStorage,
        managed_root_dir: Path,
        logger: logging.Logger,
    ) -> None:
        self._bot = bot
        self._config = config
        self._file_storage_config = file_storage_config
        self._local_storage = local_storage
        self._managed_root_dir = managed_root_dir
        self._logger = logger

    async def collect(self, message: TelegramMessage) -> tuple[list[IncomingFileRef], list[str]]:
        if not self._config.media_enabled:
            return [], []
        if not self._file_storage_config.enabled:
            return [], ["file_storage_disabled"]

        files: list[IncomingFileRef] = []
        errors: list[str] = []
        total_size = 0
        mapper = self._build_mapper(message)

        if message.photo and len(files) < self._config.max_attachments_per_message:
            photo = message.photo[-1]
            total_size = await self._collect_photo(
                mapper=mapper,
                photo=photo,
                files=files,
                errors=errors,
                total_size=total_size,
            )

        if message.document and len(files) < self._config.max_attachments_per_message:
            document = message.document
            mime_type = document.mime_type or "application/octet-stream"
            if not self._is_allowed_document_mime(mime_type):
                errors.append("document_mime_not_allowed")
                return files, errors
            total_size = await self._collect_document(
                mapper=mapper,
                document=document,
                mime_type=mime_type,
                files=files,
                errors=errors,
                total_size=total_size,
            )

        if message.audio and len(files) < self._config.max_attachments_per_message:
            audio = message.audio
            mime_type = audio.mime_type or "application/octet-stream"
            if not self._is_allowed_document_mime(mime_type):
                errors.append("audio_mime_not_allowed")
                return files, errors
            total_size = await self._collect_audio(
                mapper=mapper,
                audio=audio,
                mime_type=mime_type,
                files=files,
                errors=errors,
                total_size=total_size,
            )

        if message.voice and len(files) < self._config.max_attachments_per_message:
            voice = message.voice
            mime_type = voice.mime_type or "audio/ogg"
            if not self._is_allowed_document_mime(mime_type):
                errors.append("voice_mime_not_allowed")
                return files, errors
            total_size = await self._collect_voice(
                mapper=mapper,
                voice=voice,
                mime_type=mime_type,
                files=files,
                errors=errors,
                total_size=total_size,
            )

        return files, errors

    def _build_mapper(self, message: TelegramMessage) -> TelegramIncomingMediaMapper:
        temp_dir = self._local_storage.resolve_dir(self._file_storage_config.incoming_temp_subdir, create=True)
        return TelegramIncomingMediaMapper(
            temp_dir=temp_dir,
            chat_id=message.chat.id,
            message_id=message.message_id,
            caption=getattr(message, "caption", None),
            relative_to_root=self._relative_to_root,
            upload_filename=self._upload_filename,
        )

    async def _collect_photo(
        self,
        *,
        mapper: TelegramIncomingMediaMapper,
        photo: Any,
        files: list[IncomingFileRef],
        errors: list[str],
        total_size: int,
    ) -> int:
        photo_bytes = await self._download_media_bytes(photo)
        if photo_bytes is None:
            errors.append("photo_download_failed")
            return total_size
        limit_error = self._limit_error(
            payload_size=len(photo_bytes),
            payload_limit=self._config.max_photo_bytes,
            too_large_error="photo_too_large",
            total_size=total_size,
        )
        if limit_error is not None:
            errors.append(limit_error)
            return total_size
        saved = await self._save_uploaded_bytes(mapper.photo_target(), photo_bytes)
        if saved is None:
            return total_size
        files.append(
            mapper.to_incoming_file(
                saved=saved,
                mime="image/jpeg",
                size_bytes=len(photo_bytes),
                source="photo",
            )
        )
        return total_size + len(photo_bytes)

    async def _collect_document(
        self,
        *,
        mapper: TelegramIncomingMediaMapper,
        document: Any,
        mime_type: str,
        files: list[IncomingFileRef],
        errors: list[str],
        total_size: int,
    ) -> int:
        document_bytes = await self._download_media_bytes(document)
        if document_bytes is None:
            errors.append("document_download_failed")
            return total_size
        limit_error = self._limit_error(
            payload_size=len(document_bytes),
            payload_limit=self._config.max_document_bytes,
            too_large_error="document_too_large",
            total_size=total_size,
        )
        if limit_error is not None:
            errors.append(limit_error)
            return total_size
        saved = await self._save_uploaded_bytes(
            mapper.document_target(file_name=document.file_name, file_unique_id=document.file_unique_id),
            document_bytes,
        )
        if saved is None:
            return total_size
        files.append(
            mapper.to_incoming_file(
                saved=saved,
                mime=mime_type,
                size_bytes=len(document_bytes),
                source="document",
            )
        )
        return total_size + len(document_bytes)

    async def _collect_audio(
        self,
        *,
        mapper: TelegramIncomingMediaMapper,
        audio: Any,
        mime_type: str,
        files: list[IncomingFileRef],
        errors: list[str],
        total_size: int,
    ) -> int:
        audio_bytes = await self._download_media_bytes(audio)
        if audio_bytes is None:
            errors.append("audio_download_failed")
            return total_size
        limit_error = self._limit_error(
            payload_size=len(audio_bytes),
            payload_limit=self._config.max_document_bytes,
            too_large_error="audio_too_large",
            total_size=total_size,
        )
        if limit_error is not None:
            errors.append(limit_error)
            return total_size
        saved = await self._save_uploaded_bytes(
            mapper.audio_target(
                file_name=audio.file_name,
                file_unique_id=audio.file_unique_id,
                mime_type=mime_type,
            ),
            audio_bytes,
        )
        if saved is None:
            return total_size
        files.append(
            mapper.to_incoming_file(
                saved=saved,
                mime=mime_type,
                size_bytes=len(audio_bytes),
                source="audio",
                duration_seconds=getattr(audio, "duration", None),
            )
        )
        return total_size + len(audio_bytes)

    async def _collect_voice(
        self,
        *,
        mapper: TelegramIncomingMediaMapper,
        voice: Any,
        mime_type: str,
        files: list[IncomingFileRef],
        errors: list[str],
        total_size: int,
    ) -> int:
        voice_bytes = await self._download_media_bytes(voice)
        if voice_bytes is None:
            errors.append("voice_download_failed")
            return total_size
        limit_error = self._limit_error(
            payload_size=len(voice_bytes),
            payload_limit=self._config.max_document_bytes,
            too_large_error="voice_too_large",
            total_size=total_size,
        )
        if limit_error is not None:
            errors.append(limit_error)
            return total_size
        saved = await self._save_uploaded_bytes(mapper.voice_target(mime_type=mime_type), voice_bytes)
        if saved is None:
            return total_size
        files.append(
            mapper.to_incoming_file(
                saved=saved,
                mime=mime_type,
                size_bytes=len(voice_bytes),
                source="voice",
                duration_seconds=getattr(voice, "duration", None),
            )
        )
        return total_size + len(voice_bytes)

    def _limit_error(
        self,
        *,
        payload_size: int,
        payload_limit: int,
        too_large_error: str,
        total_size: int,
    ) -> str | None:
        if payload_size > payload_limit:
            return too_large_error
        if total_size + payload_size > self._config.max_total_media_bytes:
            return "total_media_too_large"
        return None

    async def _download_media_bytes(self, media: Any) -> bytes | None:
        buffer = io.BytesIO()
        try:
            await self._bot.download(media, destination=buffer)
        except Exception:
            self._logger.exception("telegram media download failed")
            return None
        return buffer.getvalue()

    def _is_allowed_document_mime(self, mime_type: str) -> bool:
        allowed = [entry.strip().lower() for entry in self._config.allowed_document_mime_types if entry.strip()]
        if not allowed:
            return True
        return mime_type.lower() in allowed

    async def _save_uploaded_bytes(self, path: Path, payload: bytes) -> Path | None:
        try:
            await asyncio.to_thread(path.write_bytes, payload)
        except Exception:
            self._logger.exception("failed to persist inbound telegram file", extra={"path": str(path)})
            return None
        self._logger.info("saved inbound telegram file", extra={"path": str(path), "bytes": len(payload)})
        return path

    def _relative_to_root(self, path: Path) -> str:
        return to_posix_relative(path, self._managed_root_dir)

    @staticmethod
    def _upload_filename(prefix: str, message_id: int, chat_id: int, suffix: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{prefix}_{chat_id}_{message_id}_{timestamp}{suffix}"
