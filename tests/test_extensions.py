from __future__ import annotations

import asyncio
import logging
import sys
import textwrap
from pathlib import Path

import pytest
from llm_async.models import ToolCall
from pydantic import ValidationError

from minibot.adapters.config.schema import Settings
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionContext, load_extensions
from minibot.core.channels import ChannelResponse
from minibot.core.events import OutboundEvent, TurnCompletedEvent
from minibot.llm.services.tool_executor import execute_tool_calls_for_runtime
from minibot.llm.tools.base import ToolContext

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


_DECORATED_SOURCE = """
from typing import Any

from pydantic import BaseModel, Field

from minibot.core.events import TurnCompletedEvent
from minibot.llm.tools.base import ToolContext

seen_turns = []


class GreetArgs(BaseModel):
    name: str = Field(description="Who to greet.")


def register(mb):
    @mb.tool
    async def sugar_greet(args: GreetArgs, context: ToolContext) -> dict[str, Any]:
        \"\"\"Greet someone by name.\"\"\"
        return {"ok": True, "message": f"hi, {args.name}!", "channel": context.channel}

    @mb.on(TurnCompletedEvent)
    async def _(event):
        seen_turns.append(event.turn_id)
"""


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_tool_decorator_derives_name_description_and_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_module(tmp_path, monkeypatch, "ext_sugar", _DECORATED_SOURCE)
    settings = Settings.from_dict({"extensions": {"modules": ["ext_sugar"]}})
    registry = load_extensions(settings, EventBus(), logging.getLogger("test.extensions"))

    binding = next(b for b in registry.tools if b.tool.name == "sugar_greet")
    assert binding.tool.description == "Greet someone by name."
    assert binding.tool.parameters["properties"]["name"]["description"] == "Who to greet."
    assert binding.tool.parameters["required"] == ["name"]

    # The handler receives a validated model, not the raw payload.
    assert await binding.handler({"name": "Ana"}, ToolContext(channel="console")) == {
        "ok": True,
        "message": "hi, Ana!",
        "channel": "console",
    }


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_decorated_subscription_receives_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_module(tmp_path, monkeypatch, "ext_sugar_sub", _DECORATED_SOURCE)
    settings = Settings.from_dict({"extensions": {"modules": ["ext_sugar_sub"]}})
    bus = EventBus()
    registry = load_extensions(settings, bus, logging.getLogger("test.extensions"))

    await registry.start()
    await bus.publish(TurnCompletedEvent(turn_id="turn-9", channel="console", chat_id=1))
    for _ in range(20):
        await asyncio.sleep(0.01)
        if sys.modules["ext_sugar_sub"].seen_turns:
            break
    await registry.stop()

    assert sys.modules["ext_sugar_sub"].seen_turns == ["turn-9"]


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_tool_decorator_reports_bad_arguments_as_invalid_tool_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model must be told to fix its call, not handed an opaque tool_execution_failed."""
    _write_module(tmp_path, monkeypatch, "ext_sugar_bad_args", _DECORATED_SOURCE)
    settings = Settings.from_dict({"extensions": {"modules": ["ext_sugar_bad_args"]}})
    registry = load_extensions(settings, EventBus(), logging.getLogger("test.extensions"))

    call = ToolCall(id="c1", type="function", function={"name": "sugar_greet", "arguments": "{}"})
    records = await execute_tool_calls_for_runtime(
        [call],
        registry.tools,
        ToolContext(channel="console"),
        responses_mode=False,
        logger=logging.getLogger("test.tool_executor"),
    )

    content = records[0].result.content
    assert content["ok"] is False
    assert content["error_code"] == "invalid_tool_arguments"


_NO_MODEL_SOURCE = """
def register(mb):
    @mb.tool
    async def no_model(args: dict, context) -> dict:
        \"\"\"A tool whose arguments are not a pydantic model.\"\"\"
        return {}
"""

_NO_DOC_SOURCE = """
from pydantic import BaseModel


class Args(BaseModel):
    x: int = 1


def register(mb):
    @mb.tool
    async def no_doc(args: Args, context) -> dict:
        return {}
"""


@pytest.mark.parametrize(
    ("module_name", "source", "match"),
    [
        ("ext_no_model", _NO_MODEL_SOURCE, "must be annotated with a pydantic model"),
        ("ext_no_doc", _NO_DOC_SOURCE, "needs a docstring"),
    ],
)
def test_tool_decorator_rejects_unusable_functions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module_name: str, source: str, match: str
) -> None:
    """A tool with no schema or no description is worse than a boot crash: the agent just
    quietly cannot do something. Both fail the load, naming the module."""
    _write_module(tmp_path, monkeypatch, module_name, source)
    settings = Settings.from_dict({"extensions": {"modules": [module_name]}})

    with pytest.raises(ValueError, match=match):
        load_extensions(settings, EventBus(), logging.getLogger("test.extensions"))


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
