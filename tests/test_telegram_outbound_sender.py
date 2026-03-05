from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from minibot.adapters.config.schema import TelegramChannelConfig
from minibot.adapters.messaging.telegram import outbound_sender as outbound_sender_module
from minibot.adapters.messaging.telegram.outbound_sender import TelegramOutboundSender, chunk_text
from minibot.core.channels import ChannelFileResponse, ChannelResponse, RenderableResponse
from minibot.core.events import OutboundFileEvent, OutboundFormatRepairEvent


class _BotStub:
    def __init__(self) -> None:
        self.send_message_calls: list[dict[str, Any]] = []
        self.send_document_calls: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.send_message_calls.append(kwargs)

    async def send_document(self, chat_id: int, document: Any, caption: str | None = None) -> None:
        self.send_document_calls.append({"chat_id": chat_id, "document": document, "caption": caption})


class _EventBusStub:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def _sender() -> tuple[TelegramOutboundSender, _BotStub, _EventBusStub]:
    bot = _BotStub()
    event_bus = _EventBusStub()
    sender = TelegramOutboundSender(
        bot=bot,
        event_bus=event_bus,  # type: ignore[arg-type]
        config=TelegramChannelConfig(bot_token="token"),
        logger=logging.getLogger("test.telegram.outbound"),
    )
    return sender, bot, event_bus


def test_chunk_text_splits_long_messages_preserving_limits() -> None:
    text = "line1\n" + ("x" * 4100)
    chunks = chunk_text(text, 4000)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") in text.replace("\n", "")


def test_chunk_text_returns_single_chunk_for_short_message() -> None:
    assert chunk_text("hola", 4000) == ["hola"]


@pytest.mark.asyncio
async def test_send_parse_mode_chunks_sets_markdown_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    sender, bot, _ = _sender()

    def _markdownify(value: str) -> str:
        return f"converted:{value}"

    monkeypatch.setattr(outbound_sender_module, "telegram_markdownify", _markdownify)

    success, parse_error = await sender._send_parse_mode_chunks(
        chat_id=1,
        render=RenderableResponse(kind="markdown", text="*bold*"),
    )

    assert success is True
    assert parse_error is None
    assert len(bot.send_message_calls) == 1
    assert bot.send_message_calls[0]["text"] == "converted:*bold*"
    assert bot.send_message_calls[0]["parse_mode"].value == "MarkdownV2"


@pytest.mark.asyncio
async def test_send_parse_mode_chunks_falls_back_to_plain_text_when_markdownify_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender, bot, _ = _sender()

    def _markdownify(_value: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(outbound_sender_module, "telegram_markdownify", _markdownify)

    success, parse_error = await sender._send_parse_mode_chunks(
        chat_id=1,
        render=RenderableResponse(kind="markdown", text="*bold*"),
    )

    assert success is True
    assert parse_error is None
    assert len(bot.send_message_calls) == 1
    assert bot.send_message_calls[0]["text"] == "*bold*"
    assert bot.send_message_calls[0]["parse_mode"] is None


@pytest.mark.asyncio
async def test_send_text_response_falls_back_to_plain_on_render_failure() -> None:
    sender, _, _ = _sender()
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

    sender._send_render_chunks = _send_render_chunks  # type: ignore[method-assign]

    await sender.send_text_response(response)

    assert calls == ["html", "text"]


@pytest.mark.asyncio
async def test_send_text_response_publishes_format_repair_event_on_parse_failure() -> None:
    sender, _, event_bus = _sender()
    response = ChannelResponse(
        channel="telegram",
        chat_id=1,
        text="*hello*",
        render=RenderableResponse(kind="markdown", text="*hello*"),
        metadata={"source_user_id": 2},
    )

    async def _send_render_chunks(chat_id: int, render: RenderableResponse) -> tuple[bool, str | None]:
        _ = chat_id, render
        return False, "can't parse entities"

    sender._send_render_chunks = _send_render_chunks  # type: ignore[method-assign]

    await sender.send_text_response(response)

    assert len(event_bus.events) == 1
    assert isinstance(event_bus.events[0], OutboundFormatRepairEvent)


@pytest.mark.asyncio
async def test_send_file_response_uses_send_document(tmp_path: Path) -> None:
    sender, bot, _ = _sender()
    target = tmp_path / "report.txt"
    target.write_text("hello", encoding="utf-8")

    event = OutboundFileEvent(
        response=ChannelFileResponse(channel="telegram", chat_id=1, file_path=str(target), caption="latest")
    )
    await sender.send_file_response(event)

    assert len(bot.send_document_calls) == 1
    assert bot.send_document_calls[0]["chat_id"] == 1
    assert bot.send_document_calls[0]["caption"] == "latest"
