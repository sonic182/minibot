from __future__ import annotations

import json
import time
from typing import Any

import aiosonic
from aiosonic.timeout import Timeouts

from minibot.config.schema import DecisionConfig
from minibot.core.decisions import DecisionAnswer, DecisionHTTPError, DecisionQuestion, DecisionResult


class OpenRouterDecisionClient:
    def __init__(self, config: DecisionConfig) -> None:
        self._config = config
        self._client = aiosonic.HTTPClient()

    async def ask(self, state: dict[str, Any], questions: dict[str, DecisionQuestion]) -> DecisionResult:
        body = {
            "model": self._config.model,
            "state": state,
            "questions": {
                key: question.model_dump(mode="json", exclude_none=True) for key, question in questions.items()
            },
        }
        timeouts = Timeouts(
            sock_connect=self._config.timeout_seconds,
            sock_read=self._config.timeout_seconds,
            request_timeout=self._config.timeout_seconds,
        )
        started = time.perf_counter()
        response = await self._client.post(
            self._config.base_url,
            data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeouts=timeouts,
        )
        raw = await response.content()
        latency = time.perf_counter() - started
        if response.status_code < 200 or response.status_code >= 300:
            raise DecisionHTTPError(response.status_code, raw.decode("utf-8", errors="replace"))
        payload = json.loads(raw.decode("utf-8"))
        usage = payload.get("usage") if isinstance(payload, dict) else None
        cost = usage.get("cost") if isinstance(usage, dict) else None
        return DecisionResult(
            model=str(payload["model"]),
            answers={key: DecisionAnswer.model_validate(value) for key, value in payload["answers"].items()},
            cost=float(cost) if isinstance(cost, int | float) else None,
            latency_seconds=latency,
        )
