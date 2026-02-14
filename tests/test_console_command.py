from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_console_run_once_uses_console_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.app import console as console_module

    calls: dict[str, object] = {}

    class _FakeContainer:
        @classmethod
        def configure(cls, config_path=None) -> None:
            calls["config_path"] = config_path

        @classmethod
        def get_logger(cls):
            class _Logger:
                def error(self, *_args, **_kwargs) -> None:
                    return None

            return _Logger()

        @classmethod
        def get_event_bus(cls):
            return object()

        @classmethod
        async def initialize_storage(cls) -> None:
            calls["initialized"] = True

    class _FakeDispatcher:
        def __init__(self, event_bus) -> None:
            del event_bus

        async def start(self) -> None:
            calls["dispatcher_started"] = True

        async def stop(self) -> None:
            calls["dispatcher_stopped"] = True

    class _FakeConsoleService:
        def __init__(self, event_bus, *, chat_id, user_id, console) -> None:
            del event_bus, console
            calls["chat_id"] = chat_id
            calls["user_id"] = user_id

        async def start(self) -> None:
            calls["service_started"] = True

        async def stop(self) -> None:
            calls["service_stopped"] = True

        async def publish_user_message(self, text: str) -> None:
            calls["published_text"] = text

        async def wait_for_response(self, timeout_seconds: float):
            calls["timeout"] = timeout_seconds
            return object()

    monkeypatch.setattr(console_module, "AppContainer", _FakeContainer)
    monkeypatch.setattr(console_module, "Dispatcher", _FakeDispatcher)
    monkeypatch.setattr(console_module, "ConsoleService", _FakeConsoleService)

    await console_module.run(
        once="hello",
        chat_id=42,
        user_id=7,
        timeout_seconds=3.5,
        config_path=None,
    )

    assert calls["published_text"] == "hello"
    assert calls["chat_id"] == 42
    assert calls["user_id"] == 7
    assert calls["timeout"] == 3.5
    assert calls["dispatcher_started"] is True
    assert calls["dispatcher_stopped"] is True
    assert calls["service_started"] is True
    assert calls["service_stopped"] is True


@pytest.mark.asyncio
async def test_console_run_once_reads_stdin_when_dash(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.app import console as console_module

    published: dict[str, str] = {}

    class _FakeContainer:
        @classmethod
        def configure(cls, config_path=None) -> None:
            del config_path

        @classmethod
        def get_logger(cls):
            class _Logger:
                def error(self, *_args, **_kwargs) -> None:
                    return None

            return _Logger()

        @classmethod
        def get_event_bus(cls):
            return object()

        @classmethod
        async def initialize_storage(cls) -> None:
            return None

    class _FakeDispatcher:
        def __init__(self, event_bus) -> None:
            del event_bus

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    class _FakeConsoleService:
        def __init__(self, event_bus, *, chat_id, user_id, console) -> None:
            del event_bus, chat_id, user_id, console

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        async def publish_user_message(self, text: str) -> None:
            published["text"] = text

        async def wait_for_response(self, timeout_seconds: float):
            del timeout_seconds
            return object()

    monkeypatch.setattr(console_module, "AppContainer", _FakeContainer)
    monkeypatch.setattr(console_module, "Dispatcher", _FakeDispatcher)
    monkeypatch.setattr(console_module, "ConsoleService", _FakeConsoleService)
    monkeypatch.setattr(console_module, "sys", type("S", (), {"stdin": io.StringIO("from stdin")}))

    await console_module.run(
        once="-",
        chat_id=1,
        user_id=1,
        timeout_seconds=1.0,
        config_path=None,
    )

    assert published["text"] == "from stdin"


def test_configure_console_file_only_logging_removes_stream_handlers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minibot.app.console import _configure_console_file_only_logging

    monkeypatch.chdir(tmp_path)
    logger = logging.getLogger("minibot.console.test")
    logger.handlers = []
    stream_handler = logging.StreamHandler()
    file_handler = logging.FileHandler(tmp_path / "existing.log")
    logger.handlers = [stream_handler, file_handler]

    _configure_console_file_only_logging(logger)

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.FileHandler)
