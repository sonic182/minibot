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
from minibot.core.channels import ChannelCapabilities, ChannelMessage, ChannelResponse, RenderableResponse
from minibot.core.events import (
    MessageEvent,
    OutboundEvent,
    OutboundFormatRepairEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
    TurnStopRequestedEvent,
)


def _empty_extension_registry() -> ExtensionRegistry:
    return ExtensionRegistry([], logging.getLogger("test.extensions"))


def _dispatcher_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    dispatcher_module,
    handler_cls: type,
    *,
    pending_store: object | None = None,
) -> dict[str, object]:
    store = pending_store if pending_store is not None else _FakePendingTurnStore()
    monkeypatch.setattr(dispatcher_module, "LLMMessageHandler", handler_cls)
    monkeypatch.setattr(dispatcher_module, "build_enabled_tools", lambda *args, **kwargs: [])
    return {
        "pending_turns": store,
        "settings": _FakeSettings(),
        "memory_backend": object(),
        "agent_registry": AgentRegistry([]),
        "llm_factory": object(),
        "skill_registry": SkillRegistry([]),
        "config_path": None,
        "llm_client": object(),
        "extensions": _empty_extension_registry(),
        "managed_storage": None,
    }


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
        notify_compaction_updates = "off"

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
    channel_capabilities: dict[str, ChannelCapabilities] | None = None,
    memory_backend: object | None = None,
):
    from minibot.app import dispatcher as dispatcher_module

    dependencies = _dispatcher_dependencies(monkeypatch, dispatcher_module, handler_cls, pending_store=pending_store)
    dependencies["channel_capabilities"] = channel_capabilities
    if memory_backend is not None:
        dependencies["memory_backend"] = memory_backend
    bus = EventBus()
    subscription = bus.subscribe(types=event_types)
    dispatcher = dispatcher_module.Dispatcher(bus, **dependencies)
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
        async def handle(self, event: MessageEvent, turn_input: object = None) -> ChannelResponse:
            return ChannelResponse(
                channel="telegram", chat_id=1, text=f"ok:{event.message.text}", metadata={"should_reply": True}
            )

    async with _running_dispatcher(monkeypatch, _StubHandler) as (bus, subscription):
        await bus.publish(_message_event("hello"))
        outbound = await _wait_outbound(subscription)

    assert outbound is not None
    assert outbound.response.text == "ok:hello"


@pytest.mark.asyncio
async def test_dispatcher_applies_channel_capabilities_to_messages_without_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[ChannelCapabilities] = []

    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input: object = None) -> ChannelResponse:
            seen.append(event.message.capabilities)
            return ChannelResponse(channel="telegram", chat_id=1, text="ok", metadata={"should_reply": True})

    telegram = ChannelCapabilities(supports_tool_approval=True)
    explicit = ChannelCapabilities(supports_reply_targets=True)
    explicit_event = MessageEvent(
        message=ChannelMessage(
            channel="telegram", user_id=1, chat_id=1, message_id=1, text="hi", capabilities=explicit
        ),
    )
    async with _running_dispatcher(monkeypatch, _StubHandler, channel_capabilities={"telegram": telegram}) as (
        bus,
        subscription,
    ):
        await bus.publish(_message_event("scheduled"))
        await _wait_outbound(subscription)
        await bus.publish(explicit_event)
        await _wait_outbound(subscription)

    assert seen == [telegram, explicit]


@pytest.mark.asyncio
async def test_dispatcher_records_delivered_task_results_in_history(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[tuple[str, str, str]] = []
    done = asyncio.Event()

    class _Memory:
        async def append_history(self, session_id: str, role: str, content: str, **_kwargs: object) -> None:
            recorded.append((session_id, role, content))
            done.set()

    async with _running_dispatcher(monkeypatch, _StubHandlerBase, memory_backend=_Memory()) as (bus, _):
        await bus.publish(OutboundEvent(response=ChannelResponse(channel="telegram", chat_id=7, text="plain reply")))
        await bus.publish(
            OutboundEvent(
                response=ChannelResponse(
                    channel="telegram",
                    chat_id=7,
                    text="task answer",
                    metadata={"source": "task_worker", "history_text": "Background task t1 finished."},
                )
            )
        )
        await asyncio.wait_for(done.wait(), timeout=1.0)

    assert recorded == [("telegram:7", "user", "Background task t1 finished.")]


@pytest.mark.asyncio
async def test_dispatcher_skips_outbound_when_handler_marks_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input: object = None) -> ChannelResponse:
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
        async def handle(self, event: MessageEvent, turn_input: object = None) -> ChannelResponse:
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
        async def handle(self, event: MessageEvent, turn_input: object = None) -> ChannelResponse:
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


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_dispatcher_finishes_clearing_a_replied_turn_when_stopped_mid_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clearing = asyncio.Event()

    class _SlowClearStore(_FakePendingTurnStore):
        async def clear_pending(self, event_id: str) -> None:
            if not clearing.is_set():
                clearing.set()
                await asyncio.Event().wait()
            await super().clear_pending(event_id)

    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input: object = None) -> ChannelResponse:
            return ChannelResponse(channel="telegram", chat_id=1, text="ok", metadata={"should_reply": True})

    pending_store = _SlowClearStore()
    async with _running_dispatcher(monkeypatch, _StubHandler, pending_store=pending_store) as (bus, subscription):
        event = _message_event("hello")
        await bus.publish(event)
        assert await _wait_outbound(subscription) is not None
        await asyncio.wait_for(clearing.wait(), timeout=1.0)

    assert pending_store.cleared == [event.event_id]


@pytest.mark.asyncio
async def test_dispatcher_clears_pending_turn_after_handler_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input: object = None) -> ChannelResponse:
            del event
            raise RuntimeError("provider exploded")

    pending_store = _FakePendingTurnStore()
    async with _running_dispatcher(monkeypatch, _StubHandler, pending_store=pending_store) as (bus, subscription):
        event = _message_event("hello")
        await bus.publish(event)
        outbound = await _wait_outbound(subscription)

    assert outbound is not None
    assert outbound.response.text == "Sorry, I couldn't answer right now."
    assert "exploded" not in outbound.response.text
    assert pending_store.marked == [event.event_id]
    assert pending_store.cleared == [event.event_id]


@pytest.mark.asyncio
async def test_dispatcher_publishes_compaction_update_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input: object = None) -> ChannelResponse:
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
        async def handle(self, event: MessageEvent, turn_input: object = None) -> ChannelResponse:
            if event.message.text == "boom":
                raise RuntimeError("handler exploded")
            return ChannelResponse(
                channel="telegram",
                chat_id=1,
                text="ok",
                metadata={
                    "should_reply": True,
                    "llm_provider": "openai",
                    "llm_model": "gpt-4o-mini",
                    "tools_used": ["memory"],
                    "task_handoff": True,
                },
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
                if len(collected) == 6:
                    break

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_drain(), timeout=1.0)

    started = [e for e in collected if isinstance(e, TurnStartedEvent)]
    completed = [e for e in collected if isinstance(e, TurnCompletedEvent)]
    failed = [e for e in collected if isinstance(e, TurnFailedEvent)]
    replies = [e.response.text for e in collected if isinstance(e, OutboundEvent)]
    assert "Sorry, I couldn't answer right now." in replies

    assert {e.turn_id for e in started} == {ok_event.event_id, bad_event.event_id}
    assert all(isinstance(e.available_tools, list) for e in started)
    assert len(completed) == 1
    assert completed[0].turn_id == ok_event.event_id
    assert completed[0].llm_model == "gpt-4o-mini"
    assert completed[0].tools_used == ["memory"]
    assert completed[0].task_handoff is True
    assert completed[0].should_reply is True
    assert len(failed) == 1
    assert failed[0].turn_id == bad_event.event_id
    assert "handler exploded" in failed[0].error

    # "completed" must mean delivered: the reply goes out before the turn is reported done.
    kinds = [type(e).__name__ for e in collected]
    assert kinds.index("OutboundEvent") < kinds.index("TurnCompletedEvent")


class _RecordingMemory:
    def __init__(self) -> None:
        self.history: list[tuple[str, str]] = []

    async def append_history(self, session_id: str, role: str, content: str, **_kwargs: object) -> None:
        del session_id
        self.history.append((role, content))


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_dispatcher_hands_a_message_from_the_same_chat_to_the_running_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    queued = asyncio.Event()
    handled: list[str] = []
    steered: list[str] = []

    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input=None) -> ChannelResponse:
            handled.append(event.message.text)
            if event.message.text == "search Madrid":
                started.set()
                await queued.wait()
                for queued_event in turn_input.take_all():
                    steered.append(queued_event.message.text)
                    turn_input.mark_consumed(queued_event.event_id)
            return ChannelResponse(channel="telegram", chat_id=1, text="done", metadata={"should_reply": True})

    pending_store = _FakePendingTurnStore()
    async with _running_dispatcher(monkeypatch, _StubHandler, pending_store=pending_store) as (bus, subscription):
        first = _message_event("search Madrid")
        await bus.publish(first)
        await asyncio.wait_for(started.wait(), timeout=1.0)
        second = _message_event("also Barcelona")
        await bus.publish(second)
        while second.event_id not in pending_store.marked:
            await asyncio.sleep(0.01)
        queued.set()
        replies = await _wait_outbound_messages(subscription, 2, timeout=0.3)

    assert handled == ["search Madrid"]
    assert steered == ["also Barcelona"]
    assert [reply.response.text for reply in replies] == ["done"]
    assert set(pending_store.cleared) == {first.event_id, second.event_id}


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_dispatcher_runs_a_message_the_turn_never_took_as_the_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    handled: list[str] = []

    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input=None) -> ChannelResponse:
            handled.append(event.message.text)
            if event.message.text == "first":
                started.set()
                await release.wait()
            return ChannelResponse(channel="telegram", chat_id=1, text=event.message.text, metadata={})

    async with _running_dispatcher(monkeypatch, _StubHandler) as (bus, subscription):
        await bus.publish(_message_event("first"))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await bus.publish(_message_event("late"))
        await asyncio.sleep(0.05)
        release.set()
        replies = await _wait_outbound_messages(subscription, 2)

    assert handled == ["first", "late"]
    assert [reply.response.text for reply in replies] == ["first", "late"]


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_stop_cancels_the_running_turn_and_records_it(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input=None) -> ChannelResponse:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError("unreachable")

    memory = _RecordingMemory()
    pending_store = _FakePendingTurnStore()
    event_types = (TurnFailedEvent, OutboundEvent)
    async with _running_dispatcher(
        monkeypatch, _StubHandler, pending_store=pending_store, memory_backend=memory, event_types=event_types
    ) as (bus, subscription):
        event = _message_event("research everything")
        await bus.publish(event)
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await bus.publish(_message_event("and this too"))
        while len(pending_store.marked) < 2:
            await asyncio.sleep(0.01)
        await bus.publish(TurnStopRequestedEvent(channel="telegram", chat_id=1, user_id=1))
        collected = []
        async for published in subscription:
            collected.append(published)
            if isinstance(published, OutboundEvent):
                break
        await bus.publish(TurnStopRequestedEvent(channel="telegram", chat_id=1, user_id=1))
        nothing = await _wait_outbound(subscription)

    assert cancelled.is_set()
    assert isinstance(collected[0], TurnFailedEvent)
    assert collected[0].turn_id == event.event_id
    assert collected[-1].response.text == "Stopped."
    assert memory.history == [
        ("user", "research everything"),
        ("assistant", "[Stopped by the user before finishing.]"),
        ("user", "and this too"),
    ]
    assert set(pending_store.cleared) == set(pending_store.marked)
    assert nothing is not None
    assert nothing.response.text == "Nothing is running."


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_dispatcher_keeps_a_task_result_turn_apart_from_user_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    handled: list[str] = []
    inbox_sizes: list[bool] = []

    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input=None) -> ChannelResponse:
            handled.append(event.message.text)
            if event.message.metadata.get("source") == "task_result":
                started.set()
                await release.wait()
                inbox_sizes.append(turn_input.has_pending())
            return ChannelResponse(channel="telegram", chat_id=1, text=event.message.text, metadata={})

    task_result = MessageEvent(
        message=ChannelMessage(
            channel="telegram",
            user_id=1,
            chat_id=1,
            message_id=2,
            text="worker output",
            metadata={"source": "task_result"},
        )
    )
    async with _running_dispatcher(monkeypatch, _StubHandler) as (bus, subscription):
        await bus.publish(task_result)
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await bus.publish(_message_event("my own question"))
        await asyncio.sleep(0.05)
        release.set()
        replies = await _wait_outbound_messages(subscription, 2)

    assert inbox_sizes == [False]
    assert handled == ["worker output", "my own question"]
    assert [reply.response.text for reply in replies] == ["worker output", "my own question"]


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_dispatcher_does_not_lose_a_message_that_arrives_as_the_turn_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    handled: list[str] = []

    class _SlowMarkStore(_FakePendingTurnStore):
        async def mark_pending(self, event_id: str, message_json: str) -> None:
            await super().mark_pending(event_id, message_json)
            if len(self.marked) == 2:
                release.set()
                await asyncio.sleep(0.05)

    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input=None) -> ChannelResponse:
            handled.append(event.message.text)
            if event.message.text == "first":
                started.set()
                await release.wait()
            return ChannelResponse(channel="telegram", chat_id=1, text=event.message.text, metadata={})

    async with _running_dispatcher(monkeypatch, _StubHandler, pending_store=_SlowMarkStore()) as (bus, subscription):
        await bus.publish(_message_event("first"))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await bus.publish(_message_event("just in time"))
        replies = await _wait_outbound_messages(subscription, 2)

    assert handled == ["first", "just in time"]
    assert [reply.response.text for reply in replies] == ["first", "just in time"]


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_stop_leaves_a_turn_alone_once_its_answer_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    answer_ready = asyncio.Event()
    release = asyncio.Event()

    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input=None) -> ChannelResponse:
            turn_input.answer_ready = True
            answer_ready.set()
            await release.wait()
            return ChannelResponse(channel="telegram", chat_id=1, text="the answer", metadata={})

    async with _running_dispatcher(monkeypatch, _StubHandler) as (bus, subscription):
        await bus.publish(_message_event("question"))
        await asyncio.wait_for(answer_ready.wait(), timeout=1.0)
        await bus.publish(TurnStopRequestedEvent(channel="telegram", chat_id=1, user_id=1))
        refused = await _wait_outbound(subscription)
        release.set()
        answered = await _wait_outbound(subscription)

    assert refused is not None
    assert refused.response.text == "Nothing is running."
    assert answered is not None
    assert answered.response.text == "the answer"


@pytest.mark.timeout(15)
@pytest.mark.asyncio
async def test_dispatcher_runs_waiting_messages_after_a_turn_fails_outside_the_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    handled: list[str] = []

    class _FailingClearStore(_FakePendingTurnStore):
        async def clear_pending(self, event_id: str) -> None:
            if not self.cleared:
                self.cleared.append(event_id)
                raise RuntimeError("database is locked")
            await super().clear_pending(event_id)

    class _StubHandler(_StubHandlerBase):
        async def handle(self, event: MessageEvent, turn_input=None) -> ChannelResponse:
            handled.append(event.message.text)
            if event.message.text == "first":
                started.set()
                await release.wait()
            return ChannelResponse(channel="telegram", chat_id=1, text=event.message.text, metadata={})

    store = _FailingClearStore()
    async with _running_dispatcher(monkeypatch, _StubHandler, pending_store=store) as (bus, subscription):
        await bus.publish(_message_event("first"))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        await bus.publish(_message_event("waiting"))
        while len(store.marked) < 2:
            await asyncio.sleep(0.01)
        release.set()
        replies = await _wait_outbound_messages(subscription, 2)

    assert handled == ["first", "waiting"]
    assert [reply.response.text for reply in replies] == ["first", "waiting"]
