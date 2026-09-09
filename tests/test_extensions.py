from __future__ import annotations

import asyncio
import logging
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from minibot.adapters.config.schema import Settings
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionContext, load_extensions
from minibot.core.channels import ChannelResponse
from minibot.core.events import OutboundEvent, TurnCompletedEvent

_EXTENSION_SOURCE = """
from llm_async.models import Tool

from minibot.core.events import TurnCompletedEvent
from minibot.llm.tools.base import ToolBinding

seen_turns = []


def register(mb):
    async def handler(payload, context):
        return {"ok": True, "greeting": mb.config.get("greeting", "hello")}

    mb.add_tool(
        ToolBinding(
            tool=Tool(name="demo_tool", description="demo", parameters={}),
            handler=handler,
        )
    )

    async def on_turn(event):
        seen_turns.append(event.turn_id)

    mb.on(TurnCompletedEvent, on_turn)
"""


def _write_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, source: str) -> None:
    (tmp_path / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(name, None)


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_load_extensions_collects_tools_and_delivers_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(tmp_path, monkeypatch, "ext_ok", _EXTENSION_SOURCE)
    settings = Settings.from_dict({"extensions": {"modules": ["ext_ok"], "config": {"ext_ok": {"greeting": "hola"}}}})
    bus = EventBus()
    registry = load_extensions(settings, bus, logging.getLogger("test.extensions"))

    # Bundled extensions load first, so the user module is last rather than alone.
    assert registry.names() == ["minibot.extensions.telegram", "ext_ok"]
    assert [binding.tool.name for binding in registry.tools] == ["demo_tool"]
    assert await registry.tools[0].handler({}, None) == {"ok": True, "greeting": "hola"}

    await registry.start()
    # An event the extension did not subscribe to must not reach it.
    await bus.publish(OutboundEvent(response=ChannelResponse(channel="console", chat_id=1, text="x")))
    await bus.publish(TurnCompletedEvent(turn_id="turn-7", channel="console", chat_id=1))
    for _ in range(20):
        await asyncio.sleep(0.01)
        if sys.modules["ext_ok"].seen_turns:
            break
    await registry.stop()

    assert sys.modules["ext_ok"].seen_turns == ["turn-7"]


def test_load_extensions_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()

    missing = Settings.from_dict({"extensions": {"modules": ["ext_does_not_exist"]}})
    with pytest.raises(ValueError, match="could not be imported"):
        load_extensions(missing, bus)

    _write_module(tmp_path, monkeypatch, "ext_no_register", "x = 1\n")
    no_register = Settings.from_dict({"extensions": {"modules": ["ext_no_register"]}})
    with pytest.raises(ValueError, match="does not define a callable register"):
        load_extensions(no_register, bus)

    _write_module(
        tmp_path,
        monkeypatch,
        "ext_boom",
        "def register(mb):\n    raise RuntimeError('bad wiring')\n",
    )
    boom = Settings.from_dict({"extensions": {"modules": ["ext_boom"]}})
    with pytest.raises(ValueError, match="failed during register"):
        load_extensions(boom, bus)


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_registry_starts_and_stops_contributed_services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = """
    calls = []


    class _Service:
        async def start(self):
            calls.append("start")

        async def stop(self):
            calls.append("stop")


    def register(mb):
        mb.add_service(_Service())
    """
    _write_module(tmp_path, monkeypatch, "ext_service", source)
    settings = Settings.from_dict({"extensions": {"modules": ["ext_service"]}})
    registry = load_extensions(settings, EventBus(), logging.getLogger("test.extensions"))

    await registry.start()
    assert sys.modules["ext_service"].calls == ["start"]
    await registry.stop()
    assert sys.modules["ext_service"].calls == ["start", "stop"]


_VALID_BOT_TOKEN = "123456:AAHfakefakefakefakefakefakefakefake"


@pytest.mark.parametrize(
    ("entrypoint", "channel", "expected_services"),
    [
        ("daemon", {"enabled": True, "bot_token": _VALID_BOT_TOKEN}, 1),
        ("daemon", {"enabled": False, "bot_token": _VALID_BOT_TOKEN}, 0),
        ("daemon", {"enabled": True, "bot_token": ""}, 0),
        # Console owns the only channel it runs; a built-but-unstarted service stalls the bus.
        ("console", {"enabled": True, "bot_token": _VALID_BOT_TOKEN}, 0),
    ],
)
def test_bundled_telegram_extension_registers_only_for_an_enabled_daemon(
    entrypoint: str, channel: dict[str, object], expected_services: int
) -> None:
    from minibot.extensions.telegram import register

    settings = Settings.from_dict({"channels": {"telegram": channel}})
    context = ExtensionContext(
        name="minibot.extensions.telegram",
        config={},
        settings=settings,
        event_bus=EventBus(),
        logger=logging.getLogger("test.extensions.telegram"),
        entrypoint=entrypoint,
    )
    register(context)

    assert len(context.services) == expected_services


def test_settings_allows_arbitrary_extension_config_but_still_forbids_unknown_sections() -> None:
    settings = Settings.from_dict(
        {"extensions": {"modules": ["a"], "config": {"a": {"anything": {"nested": [1, 2]}}}}}
    )
    assert settings.extensions.config["a"]["anything"] == {"nested": [1, 2]}

    with pytest.raises(ValidationError):
        Settings.from_dict({"totally_unknown_section": {"x": 1}})
