from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage, ChannelResponse, RenderableResponse
from minibot.core.events import MessageEvent, OutboundEvent, OutboundFormatRepairEvent


@dataclass
class _FakeSettings:
    class _Tools:
        class _KV:
            default_owner_id = "primary"

        kv_memory = _KV()

    class _Memory:
        max_history_messages = None

    class _Runtime:
        agent_timeout_seconds = 120

    tools = _Tools()
    memory = _Memory()
    runtime = _Runtime()


def _message_event(text: str) -> MessageEvent:
    return MessageEvent(
        message=ChannelMessage(channel="telegram", user_id=1, chat_id=1, message_id=1, text=text),
    )


async def _wait_outbound(subscription, timeout: float = 0.4) -> OutboundEvent | None:
    async def _read() -> OutboundEvent | None:
        async for event in subscription:
            if isinstance(event, OutboundEvent):
                return event
        return None

    try:
        return await asyncio.wait_for(_read(), timeout=timeout)
    except TimeoutError:
        return None


@pytest.mark.asyncio
async def test_dispatcher_publishes_outbound_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.app import dispatcher as dispatcher_module

    class _StubHandler:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def handle(self, event: MessageEvent) -> ChannelResponse:
            return ChannelResponse(
                channel="telegram", chat_id=1, text=f"ok:{event.message.text}", metadata={"should_reply": True}
            )

    monkeypatch.setattr(dispatcher_module, "LLMMessageHandler", _StubHandler)
    monkeypatch.setattr(dispatcher_module, "build_enabled_tools", lambda *args, **kwargs: [])
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_scheduled_prompt_service", lambda: None)
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_memory_backend", lambda: object())
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_kv_memory_backend", lambda: None)
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_llm_client", lambda: object())

    bus = EventBus()
    subscription = bus.subscribe()
    dispatcher = dispatcher_module.Dispatcher(bus)
    await dispatcher.start()
    await bus.publish(_message_event("hello"))

    outbound = await _wait_outbound(subscription)

    assert outbound is not None
    assert outbound.response.text == "ok:hello"
    await subscription.close()
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatcher_skips_outbound_when_handler_marks_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.app import dispatcher as dispatcher_module

    class _StubHandler:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def handle(self, event: MessageEvent) -> ChannelResponse:
            return ChannelResponse(
                channel="telegram",
                chat_id=1,
                text=f"silent:{event.message.text}",
                metadata={"should_reply": False},
            )

    monkeypatch.setattr(dispatcher_module, "LLMMessageHandler", _StubHandler)
    monkeypatch.setattr(dispatcher_module, "build_enabled_tools", lambda *args, **kwargs: [])
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_scheduled_prompt_service", lambda: None)
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_memory_backend", lambda: object())
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_kv_memory_backend", lambda: None)
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_llm_client", lambda: object())

    bus = EventBus()
    subscription = bus.subscribe()
    dispatcher = dispatcher_module.Dispatcher(bus)
    await dispatcher.start()
    await bus.publish(_message_event("hello"))

    outbound = await _wait_outbound(subscription)

    assert outbound is None
    await subscription.close()
    await dispatcher.stop()


@pytest.mark.asyncio
async def test_dispatcher_publishes_plain_fallback_when_format_repair_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minibot.app import dispatcher as dispatcher_module

    class _StubHandler:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def handle(self, event: MessageEvent) -> ChannelResponse:
            return ChannelResponse(channel="telegram", chat_id=1, text=f"ok:{event.message.text}")

        async def repair_format_response(self, **kwargs) -> ChannelResponse:
            del kwargs
            raise RuntimeError("provider timeout")

    monkeypatch.setattr(dispatcher_module, "LLMMessageHandler", _StubHandler)
    monkeypatch.setattr(dispatcher_module, "build_enabled_tools", lambda *args, **kwargs: [])
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_scheduled_prompt_service", lambda: None)
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_memory_backend", lambda: object())
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_kv_memory_backend", lambda: None)
    monkeypatch.setattr(dispatcher_module.AppContainer, "get_llm_client", lambda: object())

    bus = EventBus()
    subscription = bus.subscribe()
    dispatcher = dispatcher_module.Dispatcher(bus)
    await dispatcher.start()
    await bus.publish(
        OutboundFormatRepairEvent(
            response=ChannelResponse(
                channel="telegram",
                chat_id=1,
                text="bad markdown",
                render=RenderableResponse(kind="markdown_v2", text="*bad"),
                metadata={"source_user_id": 1},
            ),
            parse_error="can't parse entities",
            attempt=1,
            chat_id=1,
            channel="telegram",
            user_id=1,
        )
    )

    outbound = await _wait_outbound(subscription)

    assert outbound is not None
    assert outbound.response.text == "*bad"
    assert outbound.response.render is not None
    assert outbound.response.render.kind == "text"
    assert outbound.response.metadata["format_repair_failed"] is True
    assert "provider timeout" in outbound.response.metadata["format_repair_error"]
    await subscription.close()
    await dispatcher.stop()
