from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, Protocol

from minibot.core.decisions import DecisionClient, DecisionHTTPError, DecisionQuestion, DecisionResult
from minibot.llm.tools.base import ToolBinding

PendingDecision = asyncio.Task[DecisionResult | None]

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


class TurnDecision(Protocol):
    def start(self, user_text: str) -> PendingDecision | None: ...

    async def finish(
        self,
        pending: PendingDecision | None,
        *,
        session_id: str,
        tools_used: Sequence[str],
        handed_off: bool,
    ) -> None: ...

    def replace_tools(self, tools: Sequence[ToolBinding]) -> None: ...


class NoopTurnDecision:
    def start(self, user_text: str) -> PendingDecision | None:
        return None

    async def finish(
        self,
        pending: PendingDecision | None,
        *,
        session_id: str,
        tools_used: Sequence[str],
        handed_off: bool,
    ) -> None:
        return None

    def replace_tools(self, tools: Sequence[ToolBinding]) -> None:
        return None


class ShadowTurnDecision:
    def __init__(
        self,
        *,
        client: DecisionClient,
        tools: Sequence[ToolBinding],
        timeout_seconds: float,
    ) -> None:
        self._client = client
        self._tool_names = _tool_names(tools)
        self._timeout_seconds = timeout_seconds
        self._background: set[asyncio.Task[None]] = set()
        self._logger = logging.getLogger("minibot.turn_decision")

    def replace_tools(self, tools: Sequence[ToolBinding]) -> None:
        self._tool_names = _tool_names(tools)

    def start(self, user_text: str) -> PendingDecision | None:
        state = {"user_message": user_text, "available_tools": self._tool_names}
        return asyncio.create_task(self._ask(state))

    async def finish(
        self,
        pending: PendingDecision | None,
        *,
        session_id: str,
        tools_used: Sequence[str],
        handed_off: bool,
    ) -> None:
        if pending is None:
            return
        logger_task = asyncio.create_task(
            self._log_result(pending, session_id=session_id, tools_used=list(tools_used), handed_off=handed_off)
        )
        self._background.add(logger_task)
        logger_task.add_done_callback(self._background.discard)

    async def _log_result(
        self,
        pending: PendingDecision,
        *,
        session_id: str,
        tools_used: list[str],
        handed_off: bool,
    ) -> None:
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
                "session_id": session_id,
                "model": result.model,
                "latency_seconds": round(result.latency_seconds, 3),
                "cost": result.cost,
                "tools_used": tools_used,
                "handed_off": handed_off,
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


def executed_tool_names(state: Any) -> list[str]:
    if state is None:
        return []
    return sorted(
        {
            message.name
            for message in state.messages
            if message.role == "tool" and message.name not in (None, "pre_response")
        }
    )


def _tool_names(tools: Sequence[ToolBinding]) -> list[str]:
    return sorted(binding.tool.name for binding in tools)


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
