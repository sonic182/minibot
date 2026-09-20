from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import math
import mimetypes
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from minibot.adapters.config.schema import AudioTranscriptionToolConfig, FileStorageToolConfig, HTTPServerConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.core.channels import IncomingFileRef

_UPLOAD_SUBDIR = "uploads/temp/web"
_CHUNK_LIMIT = 262_144
_IMAGE_MIME_TYPES = frozenset({"image/gif", "image/jpeg", "image/png", "image/webp"})
_AUDIO_MIME_TYPES = frozenset({"audio/aac", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/webm"})


class UploadError(ValueError):
    """Raised when a browser upload cannot be accepted."""


@dataclass(frozen=True)
class ChatCapabilities:
    uploads_enabled: bool
    images_enabled: bool
    audio_enabled: bool
    max_attachments: int
    max_image_bytes: int
    max_audio_bytes: int
    max_total_bytes: int
    max_audio_duration_seconds: int

    def as_context(self) -> dict[str, bool | int]:
        return {
            "uploads_enabled": self.uploads_enabled,
            "images_enabled": self.images_enabled,
            "audio_enabled": self.audio_enabled,
            "max_attachments": self.max_attachments,
            "max_image_bytes": self.max_image_bytes,
            "max_audio_bytes": self.max_audio_bytes,
            "max_total_bytes": self.max_total_bytes,
            "max_audio_duration_seconds": self.max_audio_duration_seconds,
        }


@dataclass(frozen=True)
class PreparedUpload:
    upload_id: str
    path: str
    filename: str
    mime: str
    size_bytes: int
    kind: Literal["image", "audio"]
    duration_seconds: int | None = None

    def display(self) -> dict[str, str | int | None]:
        return {
            "id": self.upload_id,
            "kind": self.kind,
            "filename": self.filename,
            "mime": self.mime,
            "size_bytes": self.size_bytes,
            "duration_seconds": self.duration_seconds,
        }


class WebUploadSession:
    """Owns one WebSocket connection's temporary media uploads."""

    def __init__(self, manager: WebUploadManager) -> None:
        self._manager = manager
        self._active: _ActiveUpload | None = None
        self._completed: dict[str, PreparedUpload] = {}

    async def start(self, payload: dict[str, Any]) -> PreparedUpload:
        if self._active is not None:
            raise UploadError("finish or cancel the current upload first")
        if len(self._completed) >= self._manager.capabilities.max_attachments:
            raise UploadError("too many attachments")
        upload_id = _required_string(payload, "upload_id")
        if upload_id in self._completed:
            raise UploadError("upload id is already in use")
        kind = _required_kind(payload)
        filename = Path(_required_string(payload, "filename")).name or "upload"
        mime = _required_string(payload, "mime").split(";", maxsplit=1)[0].strip().lower()
        size_bytes = _required_non_negative_int(payload, "size_bytes")
        self._manager.validate_start(kind=kind, mime=mime, size_bytes=size_bytes, completed=self._completed.values())
        suffix = _suffix_for_mime(mime)
        target = await asyncio.to_thread(self._manager.create_target, suffix)
        self._active = _ActiveUpload(upload_id, target, filename, mime, size_bytes, kind)
        return PreparedUpload(upload_id, "", filename, mime, size_bytes, kind)

    async def append(self, payload: bytes) -> None:
        if self._active is None:
            raise UploadError("upload was not started")
        if not payload or len(payload) > _CHUNK_LIMIT:
            await self.cancel()
            raise UploadError("invalid upload chunk")
        if self._active.received_bytes + len(payload) > self._active.size_bytes:
            await self.cancel()
            raise UploadError("upload exceeds its declared size")
        active = self._active
        await asyncio.to_thread(_append_bytes, active.target, payload)
        active.received_bytes += len(payload)

    async def complete(self, upload_id: str) -> PreparedUpload:
        active = self._active
        if active is None or active.upload_id != upload_id:
            raise UploadError("no matching upload is active")
        if active.received_bytes != active.size_bytes:
            await self.cancel()
            raise UploadError("upload size does not match its declaration")
        try:
            prepared = await self._manager.finalize(active)
        except Exception:
            await asyncio.to_thread(active.target.unlink, missing_ok=True)
            self._active = None
            raise
        self._active = None
        self._completed[prepared.upload_id] = prepared
        return prepared

    async def cancel(self, upload_id: str | None = None) -> None:
        active = self._active
        if active is None or (upload_id is not None and active.upload_id != upload_id):
            return
        self._active = None
        await asyncio.to_thread(active.target.unlink, missing_ok=True)

    async def close(self) -> None:
        await self.cancel()

    def release(self, upload_ids: list[str]) -> None:
        for upload_id in upload_ids:
            self._completed.pop(upload_id, None)

    async def message_parts(
        self, upload_ids: list[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        if len(upload_ids) > self._manager.capabilities.max_attachments:
            raise UploadError("too many attachments")
        if len(set(upload_ids)) != len(upload_ids):
            raise UploadError("attachments must be unique")
        try:
            uploads = [self._completed[upload_id] for upload_id in upload_ids]
        except KeyError as exc:
            raise UploadError("unknown attachment") from exc
        attachments: list[dict[str, Any]] = []
        incoming_files: list[dict[str, Any]] = []
        for upload in uploads:
            incoming_files.append(
                IncomingFileRef(
                    path=upload.path,
                    filename=upload.filename,
                    mime=upload.mime,
                    size_bytes=upload.size_bytes,
                    source="photo" if upload.kind == "image" else "voice",
                    duration_seconds=upload.duration_seconds,
                ).model_dump()
            )
            if upload.kind == "image":
                data_url = await asyncio.to_thread(self._manager.image_data_url, upload)
                attachments.append({"type": "input_image", "image_url": data_url})
        return attachments, incoming_files, [upload.display() for upload in uploads]


@dataclass
class _ActiveUpload:
    upload_id: str
    target: Path
    filename: str
    mime: str
    size_bytes: int
    kind: Literal["image", "audio"]
    received_bytes: int = 0


class WebUploadManager:
    """Validates web media uploads and manages their temporary storage."""

    def __init__(
        self,
        *,
        storage: LocalFileStorage,
        http_config: HTTPServerConfig,
        file_storage_config: FileStorageToolConfig,
        audio_config: AudioTranscriptionToolConfig,
        supports_media_inputs: bool,
        logger: logging.Logger,
    ) -> None:
        self._storage = storage
        self._http_config = http_config
        self._logger = logger
        self._cleanup_task: asyncio.Task[None] | None = None
        uploads_enabled = file_storage_config.enabled
        self.capabilities = ChatCapabilities(
            uploads_enabled=uploads_enabled,
            images_enabled=uploads_enabled and supports_media_inputs,
            audio_enabled=uploads_enabled and audio_config.enabled and audio_config.auto_transcribe_short_incoming,
            max_attachments=http_config.chat_upload_max_attachments,
            max_image_bytes=http_config.chat_upload_max_image_bytes,
            max_audio_bytes=http_config.chat_upload_max_audio_bytes,
            max_total_bytes=http_config.chat_upload_max_total_bytes,
            max_audio_duration_seconds=audio_config.auto_transcribe_max_duration_seconds,
        )

    def new_session(self) -> WebUploadSession:
        return WebUploadSession(self)

    async def start(self) -> None:
        await self.cleanup_expired()
        if self._http_config.chat_upload_retention_hours > 0:
            self._cleanup_task = asyncio.create_task(self._run_cleanup())

    async def stop(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
        self._cleanup_task = None

    def create_target(self, suffix: str) -> Path:
        directory = self._storage.resolve_dir(_UPLOAD_SUBDIR, create=True)
        return directory / f"web_{secrets.token_urlsafe(18)}{suffix}"

    def validate_start(
        self,
        *,
        kind: Literal["image", "audio"],
        mime: str,
        size_bytes: int,
        completed: Any,
    ) -> None:
        if not self.capabilities.uploads_enabled:
            raise UploadError("uploads require tools.file_storage.enabled")
        if kind == "image" and not self.capabilities.images_enabled:
            raise UploadError("image uploads are unavailable for the configured model")
        if kind == "audio" and not self.capabilities.audio_enabled:
            raise UploadError("audio uploads require automatic transcription")
        allowed_mime_types = _IMAGE_MIME_TYPES if kind == "image" else _AUDIO_MIME_TYPES
        if mime not in allowed_mime_types:
            raise UploadError("unsupported media type")
        byte_limit = self.capabilities.max_image_bytes if kind == "image" else self.capabilities.max_audio_bytes
        if size_bytes > byte_limit:
            raise UploadError("attachment is too large")
        total = sum(item.size_bytes for item in completed) + size_bytes
        if total > self.capabilities.max_total_bytes:
            raise UploadError("attachments exceed the total size limit")

    async def finalize(self, active: _ActiveUpload) -> PreparedUpload:
        if active.kind == "image":
            try:
                is_valid = await asyncio.to_thread(_matches_image_signature, active.target, active.mime)
            except OSError as exc:
                raise UploadError("upload is no longer available") from exc
            if not is_valid:
                raise UploadError("image content does not match its media type")
            duration_seconds = None
        else:
            duration_seconds = await _audio_duration_seconds(active.target)
            if duration_seconds is None:
                raise UploadError("audio could not be inspected")
            if duration_seconds > self.capabilities.max_audio_duration_seconds:
                raise UploadError(f"audio exceeds {self.capabilities.max_audio_duration_seconds} seconds")
        relative_path = str(active.target.relative_to(self._storage.root_dir)).replace("\\", "/")
        return PreparedUpload(
            active.upload_id,
            relative_path,
            active.filename,
            active.mime,
            active.size_bytes,
            active.kind,
            duration_seconds,
        )

    def image_data_url(self, upload: PreparedUpload) -> str:
        target = self._storage.resolve_existing_file(upload.path)
        return f"data:{upload.mime};base64,{base64.b64encode(target.read_bytes()).decode('ascii')}"

    async def cleanup_expired(self) -> int:
        retention = self._http_config.chat_upload_retention_hours
        if retention == 0:
            return 0
        directory = self._storage.resolve_dir(_UPLOAD_SUBDIR, create=True)
        cutoff = await asyncio.to_thread(_cutoff_timestamp, retention)
        return await asyncio.to_thread(_cleanup_before, directory, cutoff)

    async def _run_cleanup(self) -> None:
        while True:
            await asyncio.sleep(3600)
            removed = await self.cleanup_expired()
            if removed:
                self._logger.info("cleaned expired web uploads", extra={"removed_count": removed})


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UploadError(f"{key} is required")
    return value.strip()


def _required_non_negative_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UploadError(f"{key} must be a non-negative integer")
    return value


def _required_kind(payload: dict[str, Any]) -> Literal["image", "audio"]:
    value = payload.get("media_kind")
    if value in {"image", "audio"}:
        return value
    raise UploadError("media_kind must be image or audio")


def _suffix_for_mime(mime: str) -> str:
    suffix = mimetypes.guess_extension(mime, strict=False)
    return suffix or ".bin"


def _append_bytes(target: Path, payload: bytes) -> None:
    with target.open("ab") as handle:
        handle.write(payload)


def _matches_image_signature(target: Path, mime: str) -> bool:
    with target.open("rb") as handle:
        header = handle.read(16)
    signatures = {
        "image/gif": header.startswith((b"GIF87a", b"GIF89a")),
        "image/jpeg": header.startswith(b"\xff\xd8\xff"),
        "image/png": header.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": header.startswith(b"RIFF") and header[8:12] == b"WEBP",
    }
    return signatures.get(mime, False)


async def _audio_duration_seconds(target: Path) -> int | None:
    try:
        async with asyncio.timeout(10):
            process = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await process.communicate()
    except (FileNotFoundError, TimeoutError):
        return None
    if process.returncode != 0:
        return None
    with contextlib.suppress(ValueError):
        duration = float(output.decode().strip())
        if duration >= 0:
            return math.ceil(duration)
    return None


def _cutoff_timestamp(retention_hours: int) -> float:
    return time.time() - retention_hours * 3600


def _cleanup_before(directory: Path, cutoff: float) -> int:
    removed = 0
    for candidate in directory.iterdir():
        if candidate.is_file() and candidate.stat().st_mtime < cutoff:
            candidate.unlink(missing_ok=True)
            removed += 1
    return removed
