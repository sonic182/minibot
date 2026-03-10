from __future__ import annotations

from typing import Any

import pytest

from minibot.llm.services.schema_fallback import complete_with_schema_fallback
from minibot.llm.services.structured_output_policy import should_retry_without_response_schema


class _FakeLogger:
    def __init__(self) -> None:
        self.warning_calls: list[tuple[str, dict[str, Any]]] = []

    def warning(self, message: str, *, extra: dict[str, Any]) -> None:
        self.warning_calls.append((message, extra))


class _FakeProvider:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def acomplete(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        if not self._responses:
            raise RuntimeError("no fake responses configured")
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_schema_fallback_retries_for_known_incompatibility() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": {"type": "object"}},
        exc=Exception("HTTP 400: JSON mode is not supported for this model"),
        mode="provider_with_fallback",
    )

    assert retry is True


def test_schema_fallback_retries_for_not_implemented_error() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": {"type": "object"}},
        exc=NotImplementedError("provider does not support structured outputs"),
        mode="provider_with_fallback",
    )

    assert retry is True


def test_schema_fallback_does_not_retry_in_provider_strict_mode() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": {"type": "object"}},
        exc=Exception("HTTP 400: JSON mode is not supported for this model"),
        mode="provider_strict",
    )

    assert retry is False


def test_schema_fallback_does_not_retry_without_schema() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": None},
        exc=Exception("HTTP 400: JSON mode is not supported for this model"),
        mode="provider_with_fallback",
    )

    assert retry is False


def test_schema_fallback_does_not_retry_for_generic_provider_error() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": {"type": "object"}},
        exc=Exception('HTTP 400: {"error":{"type":"invalid_request_error","message":"bad request"}}'),
        mode="provider_with_fallback",
    )

    assert retry is False


@pytest.mark.asyncio
async def test_complete_with_schema_fallback_retries_with_prompt_and_without_schema() -> None:
    provider = _FakeProvider(
        [
            Exception("HTTP 400: JSON mode is not supported for this model"),
            {"ok": True},
        ]
    )
    logger = _FakeLogger()

    result = await complete_with_schema_fallback(
        provider=provider,
        call_kwargs={
            "model": "test-model",
            "messages": [{"role": "system", "content": "Be helpful."}],
            "response_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
            "_structured_output_prompt_schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        },
        model="test-model",
        provider_display_name="openrouter",
        logger=logger,
        structured_output_mode="provider_with_fallback",
    )

    assert result == {"ok": True}
    assert len(provider.calls) == 2
    assert provider.calls[0]["response_schema"] is not None
    assert provider.calls[1]["response_schema"] is None
    assert "Expected shape:" in provider.calls[1]["messages"][0]["content"]
    assert "_structured_output_prompt_schema" not in provider.calls[0]
    assert "_structured_output_prompt_schema" not in provider.calls[1]
    assert logger.warning_calls


@pytest.mark.asyncio
async def test_complete_with_schema_fallback_does_not_retry_in_strict_mode() -> None:
    provider = _FakeProvider([Exception("HTTP 400: JSON mode is not supported for this model")])
    logger = _FakeLogger()

    with pytest.raises(Exception, match="JSON mode is not supported"):
        await complete_with_schema_fallback(
            provider=provider,
            call_kwargs={
                "model": "test-model",
                "messages": [{"role": "system", "content": "Be helpful."}],
                "response_schema": {"type": "object"},
            },
            model="test-model",
            provider_display_name="openrouter",
            logger=logger,
            structured_output_mode="provider_strict",
        )

    assert len(provider.calls) == 1
    assert logger.warning_calls == []
