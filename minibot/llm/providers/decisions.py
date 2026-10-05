from __future__ import annotations

import json
import time
from typing import Any

from aiosonic.timeout import Timeouts
from llm_async.providers.base import BaseProvider

from minibot.core.decisions import DecisionAnswer, DecisionHTTPError, DecisionQuestion, DecisionResult


class DecisionsProvider(BaseProvider):
    BASE_URL = "https://openrouter.ai/api/alpha"

    def __init__(self, api_key: str, base_url: str = "", *, model: str, timeout_seconds: float) -> None:
        super().__init__(api_key, base_url.rstrip("/"))
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def ask(self, state: dict[str, Any], questions: dict[str, DecisionQuestion]) -> DecisionResult:
        body = {
            "model": self.model,
            "state": state,
            "questions": {
                key: question.model_dump(mode="json", exclude_none=True) for key, question in questions.items()
            },
        }
        timeouts = Timeouts(
            sock_connect=self.timeout_seconds,
            sock_read=self.timeout_seconds,
            request_timeout=self.timeout_seconds,
        )
        started = time.perf_counter()
        response = await self.client.post(
            f"{self.base_url}/decisions",
            json=body,
            headers=self._headers_for_request({"Accept": "application/json"}),
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
