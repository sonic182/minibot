from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from minibot.adapters.config.schema import TelegramChannelConfig
from minibot.adapters.messaging.telegram.service import TelegramService
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelResponse, IncomingFileRef
from minibot.core.events import (
    MessageEvent,
    OutboundEvent,
    ToolApprovalRequestedEvent,
    ToolApprovalResolvedEvent,
)


@dataclass
class _User:
    id: int
    username: str | None = None


@dataclass
class _Chat:
    id: int


@dataclass
class _Message:
    chat: _Chat
    from_user: _User | None
    message_id: int
    text: str | None = None
    caption: str | None = None


class _BotStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _EventBusStub:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


class _CollectorStub:
    def __init__(self, *, files: list[IncomingFileRef] | None = None, errors: list[str] | None = None) -> None:
        self.files = files or []
        self.errors = errors or []
        self.calls: list[Any] = []

    async def collect(self, message: Any) -> tuple[list[IncomingFileRef], list[str]]:
        self.calls.append(message)
        return self.files, self.errors


def _service(config: TelegramChannelConfig) -> tuple[TelegramService, _BotStub, _EventBusStub, _CollectorStub]:
    service = TelegramService.__new__(TelegramService)
    bot = _BotStub()
    event_bus = _EventBusStub()
    collector = _CollectorStub(
        files=[
            IncomingFileRef(
                path="uploads/temp/a.txt",
                filename="a.txt",
                mime="text/plain",
                size_bytes=1,
                source="document",
            )
        ],
        errors=[],
    )
    service._config = config
    service._bot = bot
    service._event_bus = event_bus
    service._incoming_media_collector = collector
    service._outbound_sender = None
    service._logger = logging.getLogger("test.telegram.service")
    return service, bot, event_bus, collector


@pytest.mark.asyncio
async def test_handle_message_publishes_message_event_when_authorized() -> None:
    config = TelegramChannelConfig(bot_token="token", require_authorized=False)
    service, _, event_bus, collector = _service(config)
    message = _Message(chat=_Chat(1), from_user=_User(2, username="alice"), message_id=7, text="hello")

    await service._handle_message(message)  # type: ignore[arg-type]

    assert len(collector.calls) == 1
    assert len(event_bus.events) == 1
    assert isinstance(event_bus.events[0], MessageEvent)
    published = event_bus.events[0].message
    assert published.text == "hello"
    assert published.metadata["username"] == "alice"
    assert len(published.metadata["incoming_files"]) == 1
    assert published.metadata["incoming_files"][0]["path"] == "uploads/temp/a.txt"


@pytest.mark.asyncio
async def test_handle_message_sends_denied_response_when_unauthorized() -> None:
    config = TelegramChannelConfig(
        bot_token="token",
        allowed_user_ids=[10],
        require_authorized=False,
    )
    service, bot, event_bus, collector = _service(config)
    message = _Message(chat=_Chat(1), from_user=_User(2), message_id=7, text="hello")

    await service._handle_message(message)  # type: ignore[arg-type]

    assert not collector.calls
    assert not event_bus.events
    assert len(bot.calls) == 1
    assert "Access denied" in bot.calls[0]["text"]


class _FailingOnceSender:
    """Fails the first send the way the real bot would on a network blip: with something that is
    not a TelegramBadRequest, so nothing downstream catches it."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.failed: list[str] = []

    async def send_text_response(self, response: Any) -> None:
        if response.text == "boom":
            self.failed.append(response.text)
            raise RuntimeError("connection reset by peer")
        self.sent.append(response.text)


def _outbound(text: str) -> OutboundEvent:
    return OutboundEvent(response=ChannelResponse(channel="telegram", chat_id=1, text=text))


@pytest.mark.asyncio
async def test_outgoing_loop_survives_a_failing_send() -> None:
    # Regression: an exception escaping send_text_response ended the async-for and killed outbound
    # delivery for the rest of the process. The daemon kept receiving and answering messages and
    # silently sent none of them, then deadlocked once the 128-slot queue filled up.
    bus = EventBus()
    service = TelegramService.__new__(TelegramService)
    service._logger = logging.getLogger("test.telegram.outgoing")
    service._outgoing_subscription = bus.subscribe(types=(OutboundEvent,))
    sender = _FailingOnceSender()
    service._outbound_sender = sender

    await bus.publish(_outbound("boom"))
    await bus.publish(_outbound("delivered"))
    await service._outgoing_subscription.close()

    await service._publish_outgoing()

    assert sender.failed == ["boom"]
    assert sender.sent == ["delivered"]


@dataclass
class _Callback:
    data: str
    message: _Message
    from_user: _User
    answer: AsyncMock = field(default_factory=AsyncMock)


def _approval_service(**config: Any) -> tuple[TelegramService, _EventBusStub, _Callback]:
    service, _, event_bus, _ = _service(TelegramChannelConfig(bot_token="token", **config))
    service._pending_approvals = {"a1": (1, 7)}
    message = _Message(chat=_Chat(1), from_user=None, message_id=7)
    callback = _Callback(data="approval:a1:y", message=message, from_user=_User(2))
    return service, event_bus, callback


@pytest.mark.asyncio
async def test_approval_callback_publishes_the_answer() -> None:
    service, event_bus, callback = _approval_service(allowed_user_ids=[2])

    await service._handle_approval_callback(callback)  # type: ignore[arg-type]

    [event] = event_bus.events
    assert isinstance(event, ToolApprovalResolvedEvent)
    assert (event.approval_id, event.approved, event.user_id) == ("a1", True, 2)
    assert "a1" not in service._pending_approvals


@pytest.mark.asyncio
async def test_approval_callback_from_unauthorized_user_is_ignored() -> None:
    service, event_bus, callback = _approval_service(allowed_user_ids=[99])

    await service._handle_approval_callback(callback)  # type: ignore[arg-type]

    assert not event_bus.events
    assert "a1" in service._pending_approvals


@pytest.mark.asyncio
async def test_approval_callback_for_expired_request_is_not_published() -> None:
    service, event_bus, callback = _approval_service(allowed_user_ids=[2])
    service._pending_approvals.clear()

    await service._handle_approval_callback(callback)  # type: ignore[arg-type]

    assert not event_bus.events
    callback.answer.assert_awaited_once_with("Expired")


@pytest.mark.asyncio
async def test_approval_prompt_is_html_with_escaped_detail() -> None:
    service, bot, _, _ = _service(TelegramChannelConfig(bot_token="token"))
    sent: dict[str, Any] = {}

    async def _send(**kwargs: Any) -> Any:
        sent.update(kwargs)
        return type("Sent", (), {"message_id": 7})()

    bot.send_message = _send
    service._pending_approvals = {}

    await service._send_approval_request(
        ToolApprovalRequestedEvent(
            approval_id="a1",
            tool_name="t<b>",
            channel="telegram",
            chat_id=1,
            detail="- to:\n  - <i>x</i>\n- files:\n  - path: /a & b\n    name: c",
        )
    )

    nbsp = " "
    assert sent["parse_mode"] == "HTML"
    assert sent["text"] == (
        "🔐 Approval required\n<b>t&lt;b&gt;</b>\n\n"
        "• <b>to</b>:\n"
        f"{nbsp * 2}◦ &lt;i&gt;x&lt;/i&gt;\n"
        "• <b>files</b>:\n"
        f"{nbsp * 2}◦ <b>path</b>: /a &amp; b\n"
        f"{nbsp * 4}<b>name</b>: c"
    )


@pytest.mark.asyncio
async def test_approval_prompt_that_fails_to_send_is_denied_at_once() -> None:
    service, bot, event_bus, _ = _service(TelegramChannelConfig(bot_token="token"))

    async def _fail(**_kwargs: Any) -> None:
        raise RuntimeError("message is too long")

    bot.send_message = _fail
    service._pending_approvals = {}
    service._approval_denials = set()

    await service._send_approval_request(
        ToolApprovalRequestedEvent(approval_id="a1", tool_name="t", channel="telegram", chat_id=1, detail="d")
    )
    await asyncio.gather(*service._approval_denials)

    [event] = event_bus.events
    assert isinstance(event, ToolApprovalResolvedEvent)
    assert (event.approval_id, event.approved, event.user_id) == ("a1", False, None)
    assert "a1" not in service._pending_approvals


@pytest.mark.asyncio
async def test_failed_denial_publish_is_logged_not_left_unretrieved(caplog: pytest.LogCaptureFixture) -> None:
    service, bot, event_bus, _ = _service(TelegramChannelConfig(bot_token="token"))

    async def _fail(**_kwargs: Any) -> None:
        raise RuntimeError("message is too long")

    async def _stopped_bus(_event: Any) -> None:
        raise RuntimeError("event bus is stopped")

    bot.send_message = _fail
    event_bus.publish = _stopped_bus
    service._pending_approvals = {}
    service._approval_denials = set()

    with caplog.at_level(logging.WARNING, logger="test.telegram.service"):
        await service._send_approval_request(
            ToolApprovalRequestedEvent(approval_id="a1", tool_name="t", channel="telegram", chat_id=1, detail="d")
        )
        await asyncio.gather(*service._approval_denials, return_exceptions=True)
        await asyncio.sleep(0)

    assert any("approval denial" in record.getMessage() for record in caplog.records)
    assert not service._approval_denials


@pytest.mark.asyncio
async def test_outcome_is_still_sent_when_removing_the_buttons_fails() -> None:
    service, bot, _, _ = _service(TelegramChannelConfig(bot_token="token"))

    async def _fail(**_kwargs: Any) -> None:
        raise RuntimeError("message is not modified")

    bot.edit_message_reply_markup = _fail  # type: ignore[attr-defined]

    await service._close_approval_prompt(1, 7, "✅ Approved")

    assert [call["text"] for call in bot.calls] == ["✅ Approved"]


@pytest.mark.asyncio
async def test_stop_does_not_wait_forever_for_a_stuck_denial(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.adapters.messaging.telegram import service as service_module

    monkeypatch.setattr(service_module, "_DENIAL_DRAIN_SECONDS", 0.05)
    service, bot, _, _ = _service(TelegramChannelConfig(bot_token="token"))
    service._poll_task = None
    service._outgoing_task = None
    service._typing_tasks = {}
    bot.session = type("_Session", (), {"close": AsyncMock()})()
    stuck = asyncio.create_task(asyncio.Event().wait())
    service._approval_denials = {stuck}

    await asyncio.wait_for(service.stop(), timeout=0.5)

    assert stuck.cancelled()
