from __future__ import annotations

import asyncio
import contextlib
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, Message as TelegramMessage

from minibot.adapters.config.schema import FileStorageToolConfig, TelegramChannelConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage, ChannelResponse, IncomingFileRef, RenderableResponse
from minibot.core.events import MessageEvent, OutboundEvent, OutboundFileEvent, OutboundFormatRepairEvent
from minibot.shared.path_utils import to_posix_relative


class TelegramService:
    _MAX_MESSAGE_LENGTH = 4000

    def __init__(
        self,
        config: TelegramChannelConfig,
        event_bus: EventBus,
        file_storage_config: FileStorageToolConfig | None = None,
    ) -> None:
        self._config = config
        self._file_storage_config = file_storage_config or FileStorageToolConfig()
        self._managed_root_dir = Path(self._file_storage_config.root_dir).resolve()
        self._local_storage = LocalFileStorage(
            root_dir=self._file_storage_config.root_dir,
            max_write_bytes=self._file_storage_config.max_write_bytes,
        )
        self._event_bus = event_bus
        self._logger = logging.getLogger("minibot.telegram")
        self._bot = Bot(token=config.bot_token)
        self._dp = Dispatcher()
        self._poll_task: Optional[asyncio.Task[None]] = None
        self._outgoing_task: Optional[asyncio.Task[None]] = None
        self._outgoing_subscription = event_bus.subscribe()

        self._dp.message.register(self._handle_message)

    async def start(self) -> None:
        self._logger.info("starting telegram polling")
        self._poll_task = asyncio.create_task(self._dp.start_polling(self._bot, handle_signals=False))
        self._outgoing_task = asyncio.create_task(self._publish_outgoing())

    async def _handle_message(self, message: TelegramMessage) -> None:
        if not self._is_authorized(message):
            user_id = message.from_user.id if message.from_user else None
            chat_id = message.chat.id
            self._logger.warning(
                "blocked unauthorized sender",
                extra={"chat_id": chat_id, "user_id": user_id},
            )
            await self._bot.send_message(
                chat_id=chat_id,
                text=(f"User not recognized. Access denied. chat_id={chat_id} user_id={user_id}"),
            )
            return

        incoming_files, incoming_errors = await self._collect_incoming_files(message)
        if incoming_files:
            self._logger.info(
                "received telegram managed incoming files",
                extra={
                    "chat_id": message.chat.id,
                    "user_id": message.from_user.id if message.from_user else None,
                    "file_count": len(incoming_files),
                },
            )
        if incoming_errors:
            self._logger.warning(
                "telegram incoming media skipped",
                extra={
                    "chat_id": message.chat.id,
                    "user_id": message.from_user.id if message.from_user else None,
                    "errors": incoming_errors,
                },
            )

        text = message.text or message.caption or ""
        if not text and not incoming_files and incoming_errors:
            await self._bot.send_message(chat_id=message.chat.id, text="I could not process the attachment you sent.")
            return

        channel_message = ChannelMessage(
            channel="telegram",
            user_id=message.from_user.id if message.from_user else None,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            attachments=[],
            metadata={
                "username": getattr(message.from_user, "username", None),
                "incoming_files": [entry.model_dump() for entry in incoming_files],
                "incoming_media_errors": incoming_errors,
            },
        )
        self._logger.info(
            "received message",
            extra={
                "chat_id": message.chat.id,
                "user_id": message.from_user.id if message.from_user else None,
            },
        )
        await self._event_bus.publish(MessageEvent(message=channel_message))

    async def _collect_incoming_files(self, message: TelegramMessage) -> tuple[list[IncomingFileRef], list[str]]:
        if not self._config.media_enabled:
            return [], []
        if not self._file_storage_config.enabled:
            return [], ["file_storage_disabled"]

        files: list[IncomingFileRef] = []
        errors: list[str] = []
        total_size = 0
        temp_dir = self._local_storage.resolve_dir(self._file_storage_config.incoming_temp_subdir, create=True)

        if message.photo and len(files) < self._config.max_attachments_per_message:
            photo = message.photo[-1]
            photo_bytes = await self._download_media_bytes(photo)
            if photo_bytes is None:
                errors.append("photo_download_failed")
            elif len(photo_bytes) > self._config.max_photo_bytes:
                errors.append("photo_too_large")
            elif total_size + len(photo_bytes) > self._config.max_total_media_bytes:
                errors.append("total_media_too_large")
            else:
                photo_name = self._upload_filename(
                    prefix="photo",
                    message_id=message.message_id,
                    chat_id=message.chat.id,
                    suffix=".jpg",
                )
                saved = await self._save_uploaded_bytes(temp_dir / photo_name, photo_bytes)
                if saved is not None:
                    total_size += len(photo_bytes)
                    files.append(
                        IncomingFileRef(
                            path=self._relative_to_root(saved),
                            filename=saved.name,
                            mime="image/jpeg",
                            size_bytes=len(photo_bytes),
                            source="photo",
                            message_id=message.message_id,
                            caption=getattr(message, "caption", None),
                        )
                    )

        if message.document and len(files) < self._config.max_attachments_per_message:
            document = message.document
            mime_type = document.mime_type or "application/octet-stream"
            if not self._is_allowed_document_mime(mime_type):
                errors.append("document_mime_not_allowed")
                return files, errors
            document_bytes = await self._download_media_bytes(document)
            if document_bytes is None:
                errors.append("document_download_failed")
            elif len(document_bytes) > self._config.max_document_bytes:
                errors.append("document_too_large")
            elif total_size + len(document_bytes) > self._config.max_total_media_bytes:
                errors.append("total_media_too_large")
            else:
                base_name = Path(document.file_name or f"document_{document.file_unique_id}.bin").name
                candidate = temp_dir / base_name
                if candidate.exists():
                    candidate = temp_dir / self._upload_filename(
                        prefix="document",
                        message_id=message.message_id,
                        chat_id=message.chat.id,
                        suffix=candidate.suffix or ".bin",
                    )
                saved = await self._save_uploaded_bytes(candidate, document_bytes)
                if saved is not None:
                    total_size += len(document_bytes)
                    files.append(
                        IncomingFileRef(
                            path=self._relative_to_root(saved),
                            filename=saved.name,
                            mime=mime_type,
                            size_bytes=len(document_bytes),
                            source="document",
                            message_id=message.message_id,
                            caption=getattr(message, "caption", None),
                        )
                    )

        return files, errors

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

    async def _publish_outgoing(self) -> None:
        async for event in self._outgoing_subscription:
            if isinstance(event, OutboundEvent) and event.response.channel == "telegram":
                await self._send_text_response(event.response)
            if isinstance(event, OutboundFileEvent) and event.response.channel == "telegram":
                await self._send_file_response(event)

    async def _send_text_response(self, response: ChannelResponse) -> None:
        render = self._resolve_render(response)
        self._logger.info(
            "sending response",
            extra={"chat_id": response.chat_id, "kind": render.kind},
        )
        self._logger.debug(
            "telegram response render resolved",
            extra={
                "chat_id": response.chat_id,
                "kind": render.kind,
                "text_length": len(render.text),
                "meta_keys": sorted(render.meta.keys()),
            },
        )
        sent, parse_error = await self._send_render_chunks(chat_id=response.chat_id, render=render)
        if sent or render.kind == "text":
            return
        if self._should_trigger_format_repair(response=response, render=render, parse_error=parse_error):
            attempt = int(response.metadata.get("format_repair_attempt", 0)) + 1
            self._logger.warning(
                "telegram rich text format repair requested",
                extra={"chat_id": response.chat_id, "kind": render.kind, "attempt": attempt},
            )
            await self._event_bus.publish(
                OutboundFormatRepairEvent(
                    response=response,
                    parse_error=parse_error or "unknown parse error",
                    attempt=attempt,
                    chat_id=response.chat_id,
                    channel=response.channel,
                    user_id=self._extract_user_id(response),
                )
            )
            return
        fallback_render = RenderableResponse(kind="text", text=render.text)
        self._logger.warning(
            "telegram rich text fallback to plain text",
            extra={"chat_id": response.chat_id, "kind": render.kind},
        )
        await self._send_render_chunks(chat_id=response.chat_id, render=fallback_render)

    def _resolve_render(self, response: ChannelResponse) -> RenderableResponse:
        if response.render is None:
            return RenderableResponse(kind="text", text=response.text)
        render = response.render
        if render.kind not in {"text", "html", "markdown_v2"}:
            return RenderableResponse(kind="text", text=render.text)
        return render

    async def _send_render_chunks(self, chat_id: int, render: RenderableResponse) -> tuple[bool, str | None]:
        if render.kind == "html":
            self._logger.debug("telegram renderer applying html parse mode", extra={"chat_id": chat_id})
        elif render.kind == "markdown_v2":
            self._logger.debug("telegram renderer applying markdown_v2 parse mode", extra={"chat_id": chat_id})
        else:
            self._logger.debug("telegram renderer applying plain text mode", extra={"chat_id": chat_id})
        return await self._send_parse_mode_chunks(chat_id=chat_id, render=render)

    async def _send_parse_mode_chunks(self, chat_id: int, render: RenderableResponse) -> tuple[bool, str | None]:
        text_to_send = render.text
        parse_mode: ParseMode | None = None
        if render.kind == "html":
            parse_mode = ParseMode.HTML
        elif render.kind == "markdown_v2":
            parse_mode = ParseMode.MARKDOWN_V2

        chunks = self._chunk_text(text_to_send, self._MAX_MESSAGE_LENGTH)

        disable_preview = bool(render.meta.get("disable_link_preview", False))
        self._logger.debug(
            "prepared telegram response chunks",
            extra={
                "chat_id": chat_id,
                "kind": render.kind,
                "chunk_count": len(chunks),
                "text_length": len(text_to_send),
            },
        )
        for index, chunk in enumerate(chunks, start=1):
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_preview,
                )
            except TelegramBadRequest as exc:
                self._logger.exception(
                    "failed to send telegram response chunk",
                    exc_info=exc,
                    extra={
                        "chat_id": chat_id,
                        "kind": render.kind,
                        "chunk_index": index,
                        "chunk_length": len(chunk),
                        "chunk_count": len(chunks),
                    },
                )
                return False, str(exc)
        return True, None

    def _should_trigger_format_repair(
        self,
        *,
        response: ChannelResponse,
        render: RenderableResponse,
        parse_error: str | None,
    ) -> bool:
        if not self._config.format_repair_enabled:
            return False
        if render.kind not in {"html", "markdown_v2"}:
            return False
        if not parse_error or "can't parse entities" not in parse_error.lower():
            return False
        attempt = int(response.metadata.get("format_repair_attempt", 0))
        return attempt < self._config.format_repair_max_attempts

    @staticmethod
    def _extract_user_id(response: ChannelResponse) -> int | None:
        value = response.metadata.get("source_user_id")
        if isinstance(value, int):
            return value
        return None

    async def _send_file_response(self, event: OutboundFileEvent) -> None:
        file_path = Path(event.response.file_path)
        if not file_path.exists() or not file_path.is_file():
            self._logger.warning(
                "outbound telegram file not found",
                extra={"chat_id": event.response.chat_id, "file_path": str(file_path)},
            )
            return
        try:
            await self._bot.send_document(
                chat_id=event.response.chat_id,
                document=FSInputFile(path=str(file_path)),
                caption=event.response.caption,
            )
        except TelegramBadRequest as exc:
            self._logger.exception(
                "failed to send telegram file",
                exc_info=exc,
                extra={"chat_id": event.response.chat_id, "file_path": str(file_path)},
            )

    async def _save_uploaded_bytes(self, path: Path, payload: bytes) -> Path | None:
        try:
            path.write_bytes(payload)
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

    async def stop(self) -> None:
        if self._poll_task:
            self._logger.info("stopping telegram polling")
            with contextlib.suppress(Exception):
                await self._dp.stop_polling()
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, ValueError):
                await self._poll_task

        if self._outgoing_task:
            await self._outgoing_subscription.close()
            self._outgoing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._outgoing_task

        await self._bot.session.close()

    def _is_authorized(self, message: TelegramMessage) -> bool:
        allowed_chats = self._config.allowed_chat_ids
        allowed_users = self._config.allowed_user_ids

        chat_allowed = True if not allowed_chats else message.chat.id in allowed_chats
        user_allowed = True
        if allowed_users:
            if message.from_user:
                user_allowed = message.from_user.id in allowed_users
            else:
                user_allowed = False

        if self._config.require_authorized:
            # require both at least one list populated and match
            chat_check = chat_allowed and bool(allowed_chats)
            user_check = user_allowed and bool(allowed_users)
            # if both lists provided require both matches, else require whichever list exists
            if allowed_chats and allowed_users:
                return chat_check and user_check
            if allowed_chats:
                return chat_check
            if allowed_users:
                return user_check
            return False

        return chat_allowed and user_allowed

    @classmethod
    def _chunk_text(cls, text: str, max_length: int) -> list[str]:
        if max_length < 1:
            raise ValueError("max_length must be >= 1")
        if not text:
            return [""]
        if len(text) <= max_length:
            return [text]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= max_length:
                chunks.append(remaining)
                break

            split_at = remaining.rfind("\n", 0, max_length + 1)
            if split_at <= 0:
                split_at = max_length
            chunk = remaining[:split_at]
            chunks.append(chunk)

            remaining = remaining[split_at:]
            if remaining.startswith("\n"):
                remaining = remaining[1:]

        return chunks
