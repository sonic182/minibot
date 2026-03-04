from __future__ import annotations

from minibot.llm.services.schema_fallback import should_retry_without_response_schema


def test_schema_fallback_retries_for_openrouter_invalid_request_error() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": {"type": "object"}},
        exc=Exception('HTTP 400: {"error":{"metadata":{"raw":"{\\"type\\":\\"invalid_request_error\\"}"}}}'),
        provider_name="openrouter",
    )

    assert retry is True


def test_schema_fallback_does_not_retry_for_non_openrouter() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": {"type": "object"}},
        exc=Exception("HTTP 400: provider returned error invalid_request_error"),
        provider_name="openai",
    )

    assert retry is False


def test_schema_fallback_does_not_retry_without_schema() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": None},
        exc=Exception("HTTP 400: provider returned error invalid_request_error"),
        provider_name="openrouter",
    )

    assert retry is False
