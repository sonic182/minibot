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


class _ToolSettings:
    class _KV:
        enabled = False

    class _HTTP:
        enabled = False

    class _Time:
        enabled = False

    class _Calculator:
        enabled = False

    class _PythonExec:
        enabled = False

    class _Playwright:
        enabled = False

    kv_memory = _KV()
    http_client = _HTTP()
    time = _Time()
    calculator = _Calculator()
    python_exec = _PythonExec()
    playwright = _Playwright()


class _SchedulerSettings:
    class _Prompts:
        enabled = True

    prompts = _Prompts()


class _Settings:
    tools = _ToolSettings()
    scheduler = _SchedulerSettings()


class _TelegramConfig:
    def __init__(self, enabled: bool, bot_token: str) -> None:
        self.enabled = enabled
        self.bot_token = bot_token


@pytest.mark.asyncio
async def test_run_starts_and_stops_all_services(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.app import daemon as daemon_module

    dispatcher_probe = _Probe()
    scheduler_probe = _Probe()
    telegram_probe = _Probe()

    class _FakeContainer:
        @classmethod
        def configure(cls) -> None:
            return None

        @classmethod
        def get_logger(cls) -> _Logger:
            return _Logger()

        @classmethod
        def get_settings(cls) -> _Settings:
            return _Settings()

        @classmethod
        def get_event_bus(cls):
            return object()

        @classmethod
        def get_scheduled_prompt_service(cls) -> _Probe:
            return scheduler_probe

        @classmethod
        def get_telegram_config(cls) -> _TelegramConfig:
            return _TelegramConfig(enabled=True, bot_token="token")

        @classmethod
        async def initialize_storage(cls) -> None:
            return None

    class _FakeDispatcher:
        def __init__(self, event_bus) -> None:
            del event_bus

        async def start(self) -> None:
            await dispatcher_probe.start()

        async def stop(self) -> None:
            await dispatcher_probe.stop()

    class _FakeTelegram:
        def __init__(self, config, event_bus) -> None:
            del config, event_bus

        async def start(self) -> None:
            await telegram_probe.start()

        async def stop(self) -> None:
            await telegram_probe.stop()

    @asynccontextmanager
    async def _fake_shutdown(services, logger):
        del logger
        stop_event = asyncio.Event()
        stop_event.set()
        try:
            yield stop_event
        finally:
            for service in services:
                await service.stop()

    monkeypatch.setattr(daemon_module, "AppContainer", _FakeContainer)
    monkeypatch.setattr(daemon_module, "Dispatcher", _FakeDispatcher)
    monkeypatch.setattr(daemon_module, "TelegramService", _FakeTelegram)
    monkeypatch.setattr(daemon_module, "_graceful_shutdown", _fake_shutdown)

    await daemon_module.run()

    assert dispatcher_probe.started == 1
    assert dispatcher_probe.stopped == 1
    assert scheduler_probe.started == 1
    assert scheduler_probe.stopped == 1
    assert telegram_probe.started == 1
    assert telegram_probe.stopped == 1


@pytest.mark.asyncio
async def test_run_skips_telegram_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.app import daemon as daemon_module

    dispatcher_probe = _Probe()

    class _FakeContainer:
        @classmethod
        def configure(cls) -> None:
            return None

        @classmethod
        def get_logger(cls) -> _Logger:
            return _Logger()

        @classmethod
        def get_settings(cls) -> _Settings:
            return _Settings()

        @classmethod
        def get_event_bus(cls):
            return object()

        @classmethod
        def get_scheduled_prompt_service(cls):
            return None

        @classmethod
        def get_telegram_config(cls) -> _TelegramConfig:
            return _TelegramConfig(enabled=False, bot_token="")

        @classmethod
        async def initialize_storage(cls) -> None:
            return None

    class _FakeDispatcher:
        def __init__(self, event_bus) -> None:
            del event_bus

        async def start(self) -> None:
            await dispatcher_probe.start()

        async def stop(self) -> None:
            await dispatcher_probe.stop()

    class _NeverTelegram:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("telegram service should not be created")

    @asynccontextmanager
    async def _fake_shutdown(services, logger):
        del logger
        stop_event = asyncio.Event()
        stop_event.set()
        try:
            yield stop_event
        finally:
            for service in services:
                await service.stop()

    monkeypatch.setattr(daemon_module, "AppContainer", _FakeContainer)
    monkeypatch.setattr(daemon_module, "Dispatcher", _FakeDispatcher)
    monkeypatch.setattr(daemon_module, "TelegramService", _NeverTelegram)
    monkeypatch.setattr(daemon_module, "_graceful_shutdown", _fake_shutdown)

    await daemon_module.run()

    assert dispatcher_probe.started == 1
    assert dispatcher_probe.stopped == 1
