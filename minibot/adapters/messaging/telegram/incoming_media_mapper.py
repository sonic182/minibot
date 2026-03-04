from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Callable

from minibot.core.channels import IncomingFileRef


class TelegramIncomingMediaMapper:
    def __init__(
        self,
        *,
        temp_dir: Path,
        chat_id: int,
        message_id: int,
        caption: str | None,
        relative_to_root: Callable[[Path], str],
        upload_filename: Callable[..., str],
    ) -> None:
        self._temp_dir = temp_dir
        self._chat_id = chat_id
        self._message_id = message_id
        self._caption = caption
        self._relative_to_root = relative_to_root
        self._upload_filename = upload_filename

    def photo_target(self) -> Path:
        name = self._upload_filename(
            prefix="photo",
            message_id=self._message_id,
            chat_id=self._chat_id,
            suffix=".jpg",
        )
        return self._temp_dir / name

    def document_target(self, *, file_name: str | None, file_unique_id: str) -> Path:
        base_name = Path(file_name or f"document_{file_unique_id}.bin").name
        candidate = self._temp_dir / base_name
        if candidate.exists():
            candidate = self._temp_dir / self._upload_filename(
                prefix="document",
                message_id=self._message_id,
                chat_id=self._chat_id,
                suffix=candidate.suffix or ".bin",
            )
        return candidate

    def audio_target(self, *, file_name: str | None, file_unique_id: str, mime_type: str) -> Path:
        default_audio_name = f"audio_{file_unique_id}{self.media_suffix(mime_type)}"
        base_name = Path(file_name or default_audio_name).name
        candidate = self._temp_dir / base_name
        if candidate.exists():
            candidate = self._temp_dir / self._upload_filename(
                prefix="audio",
                message_id=self._message_id,
                chat_id=self._chat_id,
                suffix=candidate.suffix or self.media_suffix(mime_type),
            )
        return candidate

    def voice_target(self, *, mime_type: str) -> Path:
        suffix = self.media_suffix(mime_type) or ".ogg"
        name = self._upload_filename(
            prefix="voice",
            message_id=self._message_id,
            chat_id=self._chat_id,
            suffix=suffix,
        )
        return self._temp_dir / name

    def to_incoming_file(
        self,
        *,
        saved: Path,
        mime: str,
        size_bytes: int,
        source: str,
        duration_seconds: int | None = None,
    ) -> IncomingFileRef:
        return IncomingFileRef(
            path=self._relative_to_root(saved),
            filename=saved.name,
            mime=mime,
            size_bytes=size_bytes,
            source=source,
            message_id=self._message_id,
            caption=self._caption,
            duration_seconds=duration_seconds,
        )

    @staticmethod
    def media_suffix(mime_type: str) -> str:
        normalized = mime_type.strip().lower()
        if normalized == "audio/ogg":
            return ".ogg"
        suffix = mimetypes.guess_extension(mime_type, strict=False)
        if not suffix:
            return ".bin"
        return suffix
