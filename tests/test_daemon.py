from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest


class _Probe:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def debug(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None


class _EmptyPendingTurnStore:
    async def list_pending(self) -> list[tuple[str, str]]:
        return []


@pytest.mark.asyncio
async def test_run_starts_and_stops_dispatcher_and_extensions(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.app import daemon as daemon_module

    dispatcher = _Probe()
    extensions = _Probe()

    class _FakeExtensions:
        def is_empty(self) -> bool:
            return False

        def names(self) -> list[str]:
            return ["test.extension"]

        async def start(self) -> None:
            await extensions.start()

        async def stop(self) -> None:
            await extensions.stop()

    class _FakeContainer:
        @classmethod
        def configure(cls) -> None:
            return None

        @classmethod
        async def initialize_storage(cls) -> None:
            return None

        @classmethod
        def get_logger(cls) -> _Logger:
            return _Logger()

        @classmethod
        def get_settings(cls):
            return type("Settings", (), {"llm": type("LLM", (), {"strip_logs": False})()})()

        @classmethod
        def get_event_bus(cls):
            return object()

        @classmethod
        def get_extensions(cls) -> _FakeExtensions:
            return _FakeExtensions()

        @classmethod
        def get_pending_turn_store(cls) -> _EmptyPendingTurnStore:
            return _EmptyPendingTurnStore()

    class _FakeDispatcher:
        main_agent_tool_names: list[str] = []

        def __init__(self, _event_bus: object) -> None:
            pass

        async def start(self) -> None:
            await dispatcher.start()

        async def stop(self) -> None:
            await dispatcher.stop()

    @asynccontextmanager
    async def _shutdown(services, _logger):
        event = asyncio.Event()
        event.set()
        try:
            yield event
        finally:
            for service in services:
                await service.stop()

    monkeypatch.setattr(daemon_module, "AppContainer", _FakeContainer)
    monkeypatch.setattr(daemon_module, "Dispatcher", _FakeDispatcher)
    monkeypatch.setattr(daemon_module, "_graceful_shutdown", _shutdown)

    await daemon_module.run()

    assert dispatcher.started == dispatcher.stopped == 1
    assert extensions.started == extensions.stopped == 1


@pytest.mark.asyncio
async def test_replay_pending_turns_republishes_unfinished_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.app import daemon as daemon_module
    from minibot.core.channels import ChannelMessage
    from minibot.core.events import MessageEvent

    message = ChannelMessage(channel="telegram", user_id=1, chat_id=1, message_id=1, text="hi")

    class _PendingStore:
        async def list_pending(self) -> list[tuple[str, str]]:
            return [("event-123", message.model_dump_json())]

    class _Container:
        @classmethod
        def get_pending_turn_store(cls) -> _PendingStore:
            return _PendingStore()

    class _Bus:
        def __init__(self) -> None:
            self.published: list[MessageEvent] = []

        async def publish(self, event: MessageEvent) -> None:
            self.published.append(event)

    monkeypatch.setattr(daemon_module, "AppContainer", _Container)
    bus = _Bus()

    await daemon_module._replay_pending_turns(bus, _Logger())

    assert bus.published[0].event_id == "event-123"


@pytest.mark.asyncio
async def test_replay_pending_turns_noop_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.app import daemon as daemon_module

    class _Container:
        @classmethod
        def get_pending_turn_store(cls) -> _EmptyPendingTurnStore:
            return _EmptyPendingTurnStore()

    class _Bus:
        published: list[object] = []

        async def publish(self, event: object) -> None:
            self.published.append(event)

    monkeypatch.setattr(daemon_module, "AppContainer", _Container)
    bus = _Bus()

    await daemon_module._replay_pending_turns(bus, _Logger())

    assert bus.published == []
