from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from minibot.app.extensions import ExtensionContext
from minibot.core.channels import session_identifier
from minibot.core.decisions import DecisionClient, DecisionHTTPError, DecisionQuestion, DecisionResult
from minibot.core.events import TurnCompletedEvent, TurnFailedEvent, TurnStartedEvent
from minibot.llm.providers.decisions import DecisionsProvider

MAX_PENDING_TURNS = 32

TURN_QUESTIONS: dict[str, DecisionQuestion] = {
    "route": DecisionQuestion(
        type="choice",
        instructions="What should the assistant do to handle this message?",
        criteria={
            "answer_directly": "Answer from conversation context and general knowledge, no tools needed",
            "use_tools": "Needs one or more tools (memory, web, files, calculations) before answering",
            "delegate_task": "A long or multi-step job that should run as a background task",
        },
    ),
    "needs_memory": DecisionQuestion(
        type="noul",
        instructions="Does answering require recalling stored memory or past conversations?",
    ),
    "needs_web": DecisionQuestion(
        type="noul",
        instructions="Does answering require fresh information from the internet?",
    ),
    "complexity": DecisionQuestion(
        type="score",
        instructions="How complex is this request?",
        criteria=["trivial lookup or chat", "some reasoning or a single tool", "multi-step work"],
    ),
}


class _ShadowDecision:
    def __init__(self, client: DecisionClient, timeout_seconds: float) -> None:
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._pending: OrderedDict[str, asyncio.Task[DecisionResult | None]] = OrderedDict()
        self._background: set[asyncio.Task[None]] = set()
        self._logger = logging.getLogger("minibot.turn_decision")

    async def on_started(self, event: TurnStartedEvent) -> None:
        state = {"user_message": event.text, "available_tools": sorted(event.available_tools)}
        self._pending[event.turn_id] = asyncio.create_task(self._ask(state))
        while len(self._pending) > MAX_PENDING_TURNS:
            _, stale = self._pending.popitem(last=False)
            stale.cancel()

    async def on_completed(self, event: TurnCompletedEvent) -> None:
        pending = self._pending.pop(event.turn_id, None)
        if pending is None:
            return
        logger_task = asyncio.create_task(self._log_result(pending, event))
        self._background.add(logger_task)
        logger_task.add_done_callback(self._background.discard)

    async def on_failed(self, event: TurnFailedEvent) -> None:
        pending = self._pending.pop(event.turn_id, None)
        if pending is not None:
            pending.cancel()

    async def _log_result(self, pending: asyncio.Task[DecisionResult | None], event: TurnCompletedEvent) -> None:
        try:
            result = await pending
        except Exception:
            self._logger.exception("turn decision task failed")
            return
        if result is None:
            return
        self._logger.info(
            "turn decision",
            extra={
                "turn_id": event.turn_id,
                "session_id": session_identifier(event.channel, event.chat_id),
                "model": result.model,
                "latency_seconds": round(result.latency_seconds, 3),
                "cost": result.cost,
                "tools_used": list(event.tools_used),
                "handed_off": event.task_handoff,
                **_answer_fields(result),
            },
        )

    async def _ask(self, state: dict[str, Any]) -> DecisionResult | None:
        try:
            return await asyncio.wait_for(self._client.ask(state, TURN_QUESTIONS), timeout=self._timeout_seconds)
        except TimeoutError:
            self._logger.warning("turn decision timed out", extra={"timeout_seconds": self._timeout_seconds})
        except DecisionHTTPError as exc:
            self._logger.warning("turn decision request failed", extra={"status_code": exc.status_code})
        except Exception as exc:
            self._logger.warning("turn decision failed", extra={"error_type": type(exc).__name__})
        return None


def _answer_fields(result: DecisionResult) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    route = result.answers.get("route")
    if route is not None:
        fields["route"] = route.choice
        fields["route_confidence"] = _rounded(route.confidence)
        fields["route_probabilities"] = {key: _rounded(value) for key, value in route.probabilities.items()}
    for key in ("needs_memory", "needs_web"):
        answer = result.answers.get(key)
        if answer is not None:
            fields[key] = _rounded(answer.noul)
    complexity = result.answers.get("complexity")
    if complexity is not None:
        fields["complexity"] = _rounded(complexity.score)
    return fields


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def register(mb: ExtensionContext) -> None:
    config = mb.settings.decision
    if mb.entrypoint == "worker" or not config.enabled:
        return
    client = DecisionsProvider(
        config.api_key,
        config.base_url,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
    )
    shadow = _ShadowDecision(client, config.timeout_seconds)
    mb.on(TurnStartedEvent, shadow.on_started)
    mb.on(TurnCompletedEvent, shadow.on_completed)
    mb.on(TurnFailedEvent, shadow.on_failed)
