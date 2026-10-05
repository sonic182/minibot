from __future__ import annotations

import json
from typing import Any

import pytest

from minibot.config.schema import DecisionConfig
from minibot.core.decisions import DecisionHTTPError
from minibot.extensions.integrations.decision import TURN_QUESTIONS
from minibot.llm.providers.decisions import DecisionsProvider

MERCURY_RESPONSE = {
    "model": "inception/mercury-decide-20260930",
    "answers": {
        "route": {
            "type": "choice",
            "choice": "use_tools",
            "probabilities": {"answer_directly": 0.0017, "use_tools": 0.9979, "delegate_task": 0.0004},
            "confidence": 0.9972,
        },
        "needs_web": {"type": "noul", "noul": 0.97},
        "complexity": {
            "type": "score",
            "score": 0.41,
            "legend": {"0": "trivial", "1": "some", "2": "multi"},
            "probabilities": {"0": 0.67, "1": 0.25, "2": 0.08},
            "confidence": 0.39,
        },
    },
    "usage": {"input_tokens": 77, "output_tokens": 3, "cost": 0},
    "id": "gen-dec-1",
    "provider": "Inception",
}


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._body = json.dumps(payload).encode()

    async def content(self) -> bytes:
        return self._body


class _FakeHTTP:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self._response


def _client(response: _FakeResponse) -> tuple[DecisionsProvider, _FakeHTTP]:
    config = DecisionConfig(enabled=True, api_key="test-key")
    client = DecisionsProvider(
        config.api_key, config.base_url, model=config.model, timeout_seconds=config.timeout_seconds
    )
    http = _FakeHTTP(response)
    client.client = http  # type: ignore[assignment]
    return client, http


@pytest.mark.asyncio
async def test_decisions_provider_sends_typed_questions_and_parses_answers() -> None:
    client, http = _client(_FakeResponse(200, MERCURY_RESPONSE))

    result = await client.ask({"user_message": "weather in Madrid?"}, TURN_QUESTIONS)

    sent = http.calls[0]["json"]
    assert http.calls[0]["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert http.calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert sent["model"] == "~typesafe/jev-latest"
    assert sent["questions"]["needs_web"] == {"type": "noul", "instructions": TURN_QUESTIONS["needs_web"].instructions}
    assert isinstance(sent["questions"]["complexity"]["criteria"], list)
    assert result.answers["route"].choice == "use_tools"
    assert result.answers["needs_web"].noul == 0.97
    assert result.answers["complexity"].probabilities["0"] == 0.67
    assert result.cost == 0


@pytest.mark.asyncio
async def test_decisions_provider_raises_typed_error_on_http_failure() -> None:
    client, _ = _client(_FakeResponse(400, {"error": {"code": 400, "message": "bad"}}))

    with pytest.raises(DecisionHTTPError) as excinfo:
        await client.ask({"user_message": "hi"}, TURN_QUESTIONS)

    assert excinfo.value.status_code == 400
