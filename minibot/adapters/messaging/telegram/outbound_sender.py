from __future__ import annotations

import contextlib
import importlib
import logging
from pathlib import Path
from typing import Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

from aiogram import Bot
from minibot.adapters.config.schema import TelegramChannelConfig
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelResponse, RenderableResponse
from minibot.core.events import OutboundFileEvent, OutboundFormatRepairEvent

telegram_markdownify: Any | None = None


class TelegramOutboundSender:
    _MAX_MESSAGE_LENGTH = 4000

    def __init__(
        self,
        *,
        bot: Bot,
        event_bus: EventBus,
        config: TelegramChannelConfig,
        logger: logging.Logger,
    ) -> None:
        self._bot = bot
        self._event_bus = event_bus
        self._config = config
        self._logger = logger

    async def send_text_response(self, response: ChannelResponse) -> None:
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

    async def send_file_response(self, event: OutboundFileEvent) -> None:
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

    def _resolve_render(self, response: ChannelResponse) -> RenderableResponse:
        if response.render is None:
            return RenderableResponse(kind="text", text=response.text)
        render = response.render
        if render.kind not in {"text", "html", "markdown"}:
            return RenderableResponse(kind="text", text=render.text)
        return render

    async def _send_render_chunks(self, chat_id: int, render: RenderableResponse) -> tuple[bool, str | None]:
        if render.kind == "html":
            self._logger.debug("telegram renderer applying html parse mode", extra={"chat_id": chat_id})
        elif render.kind == "markdown":
            self._logger.debug("telegram renderer applying markdown parse mode", extra={"chat_id": chat_id})
        else:
            self._logger.debug("telegram renderer applying plain text mode", extra={"chat_id": chat_id})
        return await self._send_parse_mode_chunks(chat_id=chat_id, render=render)

    async def _send_parse_mode_chunks(self, chat_id: int, render: RenderableResponse) -> tuple[bool, str | None]:
        text_to_send = render.text
        parse_mode: ParseMode | None = None
        if render.kind == "html":
            parse_mode = ParseMode.HTML
        elif render.kind == "markdown":
            text_to_send, parse_mode = self._prepare_markdown_payload(chat_id=chat_id, markdown_text=render.text)

        chunks = chunk_text(text_to_send, self._MAX_MESSAGE_LENGTH)
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

    def _prepare_markdown_payload(self, *, chat_id: int, markdown_text: str) -> tuple[str, ParseMode | None]:
        markdownify = self._resolve_markdownify()
        if markdownify is None:
            self._logger.warning(
                "telegramify-markdown unavailable; using raw markdown content",
                extra={"chat_id": chat_id},
            )
            return markdown_text, ParseMode.MARKDOWN_V2
        try:
            formatted = markdownify(markdown_text)
        except Exception:
            self._logger.exception(
                "failed to convert markdown with telegramify-markdown; falling back to plain text",
                extra={"chat_id": chat_id},
            )
            return markdown_text, None
        if not isinstance(formatted, str) or not formatted:
            self._logger.warning(
                "telegramify-markdown returned invalid payload; falling back to plain text",
                extra={"chat_id": chat_id},
            )
            return markdown_text, None
        return formatted, ParseMode.MARKDOWN_V2

    @staticmethod
    def _resolve_markdownify() -> Any | None:
        global telegram_markdownify
        if telegram_markdownify is not None:
            return telegram_markdownify
        with contextlib.suppress(Exception):
            telegram_markdownify = getattr(importlib.import_module("telegramify_markdown"), "markdownify", None)
        return telegram_markdownify

    def _should_trigger_format_repair(
        self,
        *,
        response: ChannelResponse,
        render: RenderableResponse,
        parse_error: str | None,
    ) -> bool:
        if not self._config.format_repair_enabled:
            return False
        if render.kind not in {"html", "markdown"}:
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


def chunk_text(text: str, max_length: int) -> list[str]:
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
