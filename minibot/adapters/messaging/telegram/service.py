from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, Message as TelegramMessage

from minibot.adapters.config.schema import FileStorageToolConfig, TelegramChannelConfig
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent, OutboundEvent, OutboundFileEvent


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

        await self._persist_incoming_uploads(message)

        attachments, attachment_errors = await self._build_attachments(message)
        if attachments:
            self._logger.info(
                "received telegram attachments",
                extra={
                    "chat_id": message.chat.id,
                    "user_id": message.from_user.id if message.from_user else None,
                    "attachment_count": len(attachments),
                },
            )
        if attachment_errors:
            self._logger.warning(
                "telegram attachments skipped",
                extra={
                    "chat_id": message.chat.id,
                    "user_id": message.from_user.id if message.from_user else None,
                    "errors": attachment_errors,
                },
            )

        text = message.text or message.caption or ""
        if not text and not attachments and attachment_errors:
            await self._bot.send_message(chat_id=message.chat.id, text="I could not process the attachment you sent.")
            return

        channel_message = ChannelMessage(
            channel="telegram",
            user_id=message.from_user.id if message.from_user else None,
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            attachments=attachments,
            metadata={
                "username": getattr(message.from_user, "username", None),
                "attachment_errors": attachment_errors,
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

    async def _build_attachments(self, message: TelegramMessage) -> tuple[list[dict[str, Any]], list[str]]:
        if not self._config.media_enabled:
            return [], []

        attachments: list[dict[str, Any]] = []
        errors: list[str] = []
        total_size = 0

        if message.photo and len(attachments) < self._config.max_attachments_per_message:
            photo = message.photo[-1]
            photo_bytes = await self._download_media_bytes(photo)
            if photo_bytes is None:
                errors.append("photo_download_failed")
            elif len(photo_bytes) > self._config.max_photo_bytes:
                errors.append("photo_too_large")
            elif total_size + len(photo_bytes) > self._config.max_total_media_bytes:
                errors.append("total_media_too_large")
            else:
                data_url = self._to_data_url(photo_bytes, "image/jpeg")
                attachments.append({"type": "input_image", "image_url": data_url})
                total_size += len(photo_bytes)
                self._logger.debug(
                    "telegram photo converted to input_image",
                    extra={
                        "photo_bytes": len(photo_bytes),
                    },
                )

        if message.document and len(attachments) < self._config.max_attachments_per_message:
            document = message.document
            mime_type = document.mime_type or "application/octet-stream"
            if not self._is_allowed_document_mime(mime_type):
                errors.append("document_mime_not_allowed")
                return attachments, errors

            document_bytes = await self._download_media_bytes(document)
            if document_bytes is None:
                errors.append("document_download_failed")
            elif len(document_bytes) > self._config.max_document_bytes:
                errors.append("document_too_large")
            elif total_size + len(document_bytes) > self._config.max_total_media_bytes:
                errors.append("total_media_too_large")
            else:
                filename = document.file_name or f"document_{document.file_unique_id}"
                if mime_type.lower().startswith("image/"):
                    attachments.append(
                        {
                            "type": "input_image",
                            "image_url": self._to_data_url(document_bytes, mime_type),
                        }
                    )
                    self._logger.debug(
                        "telegram document converted to input_image",
                        extra={
                            "document_name": filename,
                            "mime_type": mime_type,
                            "document_bytes": len(document_bytes),
                        },
                    )
                else:
                    attachments.append(
                        {
                            "type": "input_file",
                            "filename": filename,
                            "file_data": self._to_data_url(document_bytes, mime_type),
                        }
                    )
                    self._logger.debug(
                        "telegram document converted to input_file",
                        extra={
                            "document_name": filename,
                            "mime_type": mime_type,
                            "document_bytes": len(document_bytes),
                            "file_data_prefix": f"data:{mime_type};base64,",
                        },
                    )
                total_size += len(document_bytes)

        return attachments, errors

    async def _download_media_bytes(self, media: Any) -> bytes | None:
        buffer = io.BytesIO()
        try:
            await self._bot.download(media, destination=buffer)
        except Exception:
            self._logger.exception("telegram media download failed")
            return None
        return buffer.getvalue()

    @staticmethod
    def _to_data_url(content: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def _is_allowed_document_mime(self, mime_type: str) -> bool:
        allowed = [entry.strip().lower() for entry in self._config.allowed_document_mime_types if entry.strip()]
        if not allowed:
            return True
        return mime_type.lower() in allowed

    async def _publish_outgoing(self) -> None:
        async for event in self._outgoing_subscription:
            if isinstance(event, OutboundEvent) and event.response.channel == "telegram":
                self._logger.info("sending response", extra={"chat_id": event.response.chat_id})
                chunks = self._chunk_text(event.response.text, self._MAX_MESSAGE_LENGTH)
                self._logger.debug(
                    "prepared telegram response chunks",
                    extra={
                        "chat_id": event.response.chat_id,
                        "chunk_count": len(chunks),
                        "text_length": len(event.response.text),
                    },
                )
                for index, chunk in enumerate(chunks, start=1):
                    try:
                        await self._bot.send_message(chat_id=event.response.chat_id, text=chunk)
                    except TelegramBadRequest as exc:
                        self._logger.exception(
                            "failed to send telegram response chunk",
                            exc_info=exc,
                            extra={
                                "chat_id": event.response.chat_id,
                                "chunk_index": index,
                                "chunk_length": len(chunk),
                                "chunk_count": len(chunks),
                            },
                        )
                        break
            if isinstance(event, OutboundFileEvent) and event.response.channel == "telegram":
                await self._send_file_response(event)

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

    async def _persist_incoming_uploads(self, message: TelegramMessage) -> None:
        if not self._file_storage_config.enabled or not self._file_storage_config.save_incoming_uploads:
            return

        upload_dir = (self._managed_root_dir / self._file_storage_config.uploads_subdir).resolve()
        if not upload_dir.is_relative_to(self._managed_root_dir):
            self._logger.warning(
                "invalid uploads directory configuration",
                extra={"uploads_subdir": self._file_storage_config.uploads_subdir},
            )
            return
        upload_dir.mkdir(parents=True, exist_ok=True)

        if message.photo:
            photo = message.photo[-1]
            photo_bytes = await self._download_media_bytes(photo)
            if photo_bytes is not None:
                photo_name = self._upload_filename(
                    prefix="photo",
                    message_id=message.message_id,
                    chat_id=message.chat.id,
                    suffix=".jpg",
                )
                await self._save_uploaded_bytes(upload_dir / photo_name, photo_bytes)

        if message.document:
            document = message.document
            document_bytes = await self._download_media_bytes(document)
            if document_bytes is not None:
                filename = document.file_name or self._upload_filename(
                    prefix="document",
                    message_id=message.message_id,
                    chat_id=message.chat.id,
                    suffix=".bin",
                )
                safe_name = Path(filename).name
                target = upload_dir / safe_name
                if target.exists():
                    target = upload_dir / self._upload_filename(
                        prefix="document",
                        message_id=message.message_id,
                        chat_id=message.chat.id,
                        suffix=target.suffix or ".bin",
                    )
                await self._save_uploaded_bytes(target, document_bytes)

    async def _save_uploaded_bytes(self, path: Path, payload: bytes) -> None:
        try:
            path.write_bytes(payload)
        except Exception:
            self._logger.exception("failed to persist inbound telegram file", extra={"path": str(path)})
            return
        self._logger.info("saved inbound telegram file", extra={"path": str(path), "bytes": len(payload)})

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
