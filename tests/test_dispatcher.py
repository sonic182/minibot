from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

import pytest

from minibot.app.agent_registry import AgentRegistry
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionRegistry
from minibot.app.skill_registry import SkillRegistry
from minibot.core.channels import ChannelMessage, ChannelResponse, RenderableResponse
from minibot.core.events import (
    MessageEvent,
    OutboundEvent,
    OutboundFormatRepairEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)


def _empty_extension_registry() -> ExtensionRegistry:
    return ExtensionRegistry([], logging.getLogger("test.extensions"))


def _patch_container(
    monkeypatch: pytest.MonkeyPatch,
    dispatcher_module,
    handler_cls: type,
    *,
    pending_store: object | None = None,
) -> None:
    """Stub every AppContainer getter Dispatcher.__init__ reaches for."""
    store = pending_store if pending_store is not None else _FakePendingTurnStore()
    monkeypatch.setattr(dispatcher_module, "LLMMessageHandler", handler_cls)
    monkeypatch.setattr(dispatcher_module, "build_enabled_tools", lambda *args, **kwargs: [])
    container = dispatcher_module.AppContainer
    monkeypatch.setattr(container, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(container, "get_scheduled_prompt_service", lambda: None)
    monkeypatch.setattr(container, "get_memory_backend", lambda: object())
    monkeypatch.setattr(container, "get_kv_memory_backend", lambda: None)
    monkeypatch.setattr(container, "get_llm_client", lambda: object())
    monkeypatch.setattr(container, "get_agent_registry", lambda: AgentRegistry([]))
    monkeypatch.setattr(container, "get_skill_registry", lambda: SkillRegistry([]))
    monkeypatch.setattr(container, "get_llm_factory", lambda: object())
    monkeypatch.setattr(container, "get_pending_turn_store", lambda: store)
    monkeypatch.setattr(container, "get_extensions", _empty_extension_registry)


class _FakePendingTurnStore:
    def __init__(self) -> None:
        self.marked: list[str] = []
        self.cleared: list[str] = []

    async def mark_pending(self, event_id: str, message_json: str) -> None:
        del message_json
        self.marked.append(event_id)

    async def clear_pending(self, event_id: str) -> None:
        self.cleared.append(event_id)


@dataclass
class _FakeSettings:
    class _Tools:
        class _KV:
            enabled = False

        class _Browser:
            output_dir = "./data/files/browser"

        class _MCP:
            enabled = False
            name_prefix = "mcp"

        class _FileStorage:
            enabled = False
            root_dir = "./data/files"

        class _Skills:
            preload_catalog = False

        kv_memory = _KV()
        browser = _Browser()
        mcp = _MCP()
        file_storage = _FileStorage()
        skills = _Skills()

    class _Memory:
        max_history_messages = None
        max_history_tokens = None
        notify_compaction_updates = False

    class _Runtime:
        agent_timeout_seconds = 120
        owner_id = "primary"

    class _Orchestration:
        class _MainAgent:
            tools_allow: list[str] = []
            tools_deny: list[str] = []

        default_timeout_seconds = 90
        tool_ownership_mode = "shared"
        main_tool_use_guardrail = "disabled"
        main_agent = _MainAgent()

    tools = _Tools()
    memory = _Memory()
    runtime = _Runtime()
    orchestration = _Orchestration()


class _StubHandlerBase:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs


@contextlib.asynccontextmanager
async def _running_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
    handler_cls: type,
    *,
    pending_store: object | None = None,
    event_types: tuple[type, ...] | None = None,
):
    from minibot.app import dispatcher as dispatcher_module

    _patch_container(monkeypatch, dispatcher_module, handler_cls, pending_store=pending_store)
    bus = EventBus()
    subscription = bus.subscribe(types=event_types)
    dispatcher = dispatcher_module.Dispatcher(bus)
    await dispatcher.start()
    try:
        yield bus, subscription
    finally:
        await subscription.close()
        await dispatcher.stop()


def _message_event(text: str) -> MessageEvent:
    return MessageEvent(
        message=ChannelMessage(channel="telegram", user_id=1, chat_id=1, message_id=1, text=text),
    )


async def _next_outbound(subscription) -> OutboundEvent | None:
    async for event in subscription:
        if isinstance(event, OutboundEvent):
            return event
    return None


async def _wait_outbound_messages(subscription, count: int, timeout: float = 0.6) -> list[OutboundEvent]:
    results: list[OutboundEvent] = []
    try:
        while len(results) < count:
            event = await asyncio.wait_for(_next_outbound(subscription), timeout=timeout)
            if event is None:
                break
            results.append(event)
    except TimeoutError:
        return results
    return results


async def _wait_outbound(subscription, timeout: float = 0.4) -> OutboundEvent | None:
    try:
        return await asyncio.wait_for(_next_outbound(subscription), timeout=timeout)
    except TimeoutError:
        return None


@pytest.mark.asyncio
async def test_dispatcher_publishes_outbound_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent) -> ChannelResponse:
            return ChannelResponse(
                channel="telegram", chat_id=1, text=f"ok:{event.message.text}", metadata={"should_reply": True}
            )

    async with _running_dispatcher(monkeypatch, _StubHandler) as (bus, subscription):
        await bus.publish(_message_event("hello"))
        outbound = await _wait_outbound(subscription)

    assert outbound is not None
    assert outbound.response.text == "ok:hello"


@pytest.mark.asyncio
async def test_dispatcher_skips_outbound_when_handler_marks_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent) -> ChannelResponse:
            return ChannelResponse(
                channel="telegram",
                chat_id=1,
                text=f"silent:{event.message.text}",
                metadata={"should_reply": False},
            )

    async with _running_dispatcher(monkeypatch, _StubHandler) as (bus, subscription):
        await bus.publish(_message_event("hello"))
        outbound = await _wait_outbound(subscription)

    assert outbound is None


@pytest.mark.asyncio
async def test_dispatcher_publishes_plain_fallback_when_format_repair_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent) -> ChannelResponse:
            return ChannelResponse(channel="telegram", chat_id=1, text=f"ok:{event.message.text}")

        async def repair_format_response(self, **kwargs) -> ChannelResponse:
            del kwargs
            raise RuntimeError("provider timeout")

    async with _running_dispatcher(monkeypatch, _StubHandler) as (bus, subscription):
        await bus.publish(
            OutboundFormatRepairEvent(
                response=ChannelResponse(
                    channel="telegram",
                    chat_id=1,
                    text="bad markdown",
                    render=RenderableResponse(kind="markdown", text="*bad"),
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


@pytest.mark.asyncio
async def test_dispatcher_marks_and_clears_pending_turn_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent) -> ChannelResponse:
            return ChannelResponse(
                channel="telegram", chat_id=1, text=f"ok:{event.message.text}", metadata={"should_reply": True}
            )

    pending_store = _FakePendingTurnStore()
    async with _running_dispatcher(monkeypatch, _StubHandler, pending_store=pending_store) as (bus, subscription):
        event = _message_event("hello")
        await bus.publish(event)
        outbound = await _wait_outbound(subscription)

    assert outbound is not None
    assert pending_store.marked == [event.event_id]
    assert pending_store.cleared == [event.event_id]


@pytest.mark.asyncio
async def test_dispatcher_clears_pending_turn_after_handler_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent) -> ChannelResponse:
            del event
            raise RuntimeError("provider exploded")

    pending_store = _FakePendingTurnStore()
    async with _running_dispatcher(monkeypatch, _StubHandler, pending_store=pending_store) as (bus, subscription):
        event = _message_event("hello")
        await bus.publish(event)
        outbound = await _wait_outbound(subscription)

    assert outbound is None
    assert pending_store.marked == [event.event_id]
    assert pending_store.cleared == [event.event_id]


@pytest.mark.asyncio
async def test_dispatcher_publishes_compaction_update_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent) -> ChannelResponse:
            return ChannelResponse(
                channel="telegram",
                chat_id=1,
                text=f"ok:{event.message.text}",
                metadata={
                    "should_reply": True,
                    "compaction_updates": ["running compaction...", "done compacting", "compacted summary"],
                },
            )

    async with _running_dispatcher(monkeypatch, _StubHandler) as (bus, subscription):
        await bus.publish(_message_event("hello"))
        outbound = await _wait_outbound_messages(subscription, 4)

    assert [event.response.text for event in outbound] == [
        "ok:hello",
        "running compaction...",
        "done compacting",
        "compacted summary",
    ]


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_dispatcher_publishes_turn_lifecycle_events(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent) -> ChannelResponse:
            if event.message.text == "boom":
                raise RuntimeError("handler exploded")
            return ChannelResponse(
                channel="telegram",
                chat_id=1,
                text="ok",
                metadata={"should_reply": True, "llm_provider": "openai", "llm_model": "gpt-4o-mini"},
            )

    event_types = (TurnStartedEvent, TurnCompletedEvent, TurnFailedEvent, OutboundEvent)
    async with _running_dispatcher(monkeypatch, _StubHandler, event_types=event_types) as (bus, subscription):
        ok_event = _message_event("hello")
        bad_event = _message_event("boom")
        await bus.publish(ok_event)
        await bus.publish(bad_event)

        collected = []

        async def _drain() -> None:
            async for event in subscription:
                collected.append(event)
                if len(collected) == 5:
                    break

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_drain(), timeout=1.0)

    started = [e for e in collected if isinstance(e, TurnStartedEvent)]
    completed = [e for e in collected if isinstance(e, TurnCompletedEvent)]
    failed = [e for e in collected if isinstance(e, TurnFailedEvent)]

    assert {e.turn_id for e in started} == {ok_event.event_id, bad_event.event_id}
    assert len(completed) == 1
    assert completed[0].turn_id == ok_event.event_id
    assert completed[0].llm_model == "gpt-4o-mini"
    assert completed[0].should_reply is True
    assert len(failed) == 1
    assert failed[0].turn_id == bad_event.event_id
    assert "handler exploded" in failed[0].error

    # "completed" must mean delivered: the reply goes out before the turn is reported done.
    kinds = [type(e).__name__ for e in collected]
    assert kinds.index("OutboundEvent") < kinds.index("TurnCompletedEvent")
