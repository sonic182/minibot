from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest

from minibot.adapters.config.schema import Settings
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionContext, ExtensionRegistry
from minibot.core.decisions import DecisionAnswer, DecisionResult
from minibot.core.events import TurnCompletedEvent, TurnFailedEvent, TurnInputPreparedEvent
from minibot.extensions.integrations import decision


class _RecordCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def decision_records() -> Iterator[list[logging.LogRecord]]:
    collector = _RecordCollector()
    logger = logging.getLogger("minibot.turn_decision")
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(collector)
    yield collector.records
    logger.removeHandler(collector)
    logger.setLevel(previous_level)


def _context(settings: Settings, bus: EventBus, entrypoint: str = "console") -> ExtensionContext:
    return ExtensionContext(
        name="minibot.extensions.integrations.decision",
        config={},
        settings=settings,
        event_bus=bus,
        logger=logging.getLogger("test.extensions.decision"),
        entrypoint=entrypoint,  # type: ignore[arg-type]
    )


async def _run_turn(
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncMock,
    *,
    finish: TurnCompletedEvent | TurnFailedEvent,
    prepared_text: str | None = "ping",
) -> None:
    monkeypatch.setattr(decision, "DecisionsProvider", lambda *args, **kwargs: client)
    bus = EventBus()
    context = _context(Settings.from_dict({"decision": {"enabled": True, "api_key": "key"}}), bus)
    decision.register(context)
    registry = ExtensionRegistry([context], logging.getLogger("test.extensions.decision"))
    await registry.start()
    try:
        if prepared_text is not None:
            prepared = TurnInputPreparedEvent(
                turn_id="turn-1", channel="console", chat_id=1, text=prepared_text, available_tools=["b", "a"]
            )
            await bus.publish(prepared)
        await bus.publish(finish)
        for _ in range(20):
            await asyncio.sleep(0.01)
    finally:
        await registry.stop()


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_decision_extension_logs_the_choice_next_to_what_the_turn_did(
    monkeypatch: pytest.MonkeyPatch, decision_records: list[logging.LogRecord]
) -> None:
    client = AsyncMock()
    client.ask.return_value = DecisionResult(
        model="inception/mercury-decide-20260930",
        answers={
            "route": DecisionAnswer(
                type="choice", choice="answer_directly", probabilities={"answer_directly": 0.98}, confidence=0.98
            ),
            "needs_web": DecisionAnswer(type="noul", noul=0.02),
        },
        cost=0.0,
        latency_seconds=0.31,
    )

    await _run_turn(
        monkeypatch,
        client,
        finish=TurnCompletedEvent(turn_id="turn-1", channel="console", chat_id=1, tools_used=["memory"]),
    )

    assert client.ask.await_args.args[0] == {"user_message": "ping", "available_tools": ["a", "b"]}
    record = next(item for item in decision_records if item.getMessage() == "turn decision")
    assert record.turn_id == "turn-1"
    assert record.route == "answer_directly"
    assert record.needs_web == 0.02
    assert record.tools_used == ["memory"]
    assert record.handed_off is False


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_decision_extension_failure_is_only_logged(
    monkeypatch: pytest.MonkeyPatch, decision_records: list[logging.LogRecord]
) -> None:
    client = AsyncMock()
    client.ask.side_effect = RuntimeError("decision backend down")

    await _run_turn(monkeypatch, client, finish=TurnCompletedEvent(turn_id="turn-1", channel="console", chat_id=1))

    assert [item.getMessage() for item in decision_records] == ["turn decision failed"]


@pytest.mark.timeout(10)
@pytest.mark.asyncio
async def test_decision_extension_skips_turns_that_never_prepared_their_input(
    monkeypatch: pytest.MonkeyPatch, decision_records: list[logging.LogRecord]
) -> None:
    client = AsyncMock()

    await _run_turn(
        monkeypatch,
        client,
        finish=TurnFailedEvent(turn_id="turn-1", channel="console", chat_id=1, error="boom"),
        prepared_text=None,
    )

    client.ask.assert_not_awaited()
    assert decision_records == []


@pytest.mark.parametrize(
    ("entrypoint", "enabled", "expected_subscriptions"),
    [("console", True, 3), ("daemon", True, 3), ("worker", True, 0), ("console", False, 0)],
)
def test_decision_extension_subscribes_only_when_enabled_outside_workers(
    entrypoint: str, enabled: bool, expected_subscriptions: int
) -> None:
    settings = Settings.from_dict({"decision": {"enabled": enabled, "api_key": "key"}})
    context = _context(settings, EventBus(), entrypoint)

    decision.register(context)

    assert len(context.subscriptions) == expected_subscriptions
