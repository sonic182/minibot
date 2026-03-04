from __future__ import annotations

from minibot.llm.services.schema_fallback import should_retry_without_response_schema


def test_schema_fallback_retries_for_openrouter_json_mode_not_supported() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": {"type": "object"}},
        exc=Exception("HTTP 400: JSON mode is not supported for this model"),
        provider_name="openrouter",
    )

    assert retry is True


def test_schema_fallback_retries_for_openrouter_code_20024() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": {"type": "object"}},
        exc=Exception('HTTP 400: {"error":{"code":20024,"message":"schema unsupported"}}'),
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


def test_schema_fallback_does_not_retry_for_generic_openrouter_invalid_request_error() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": {"type": "object"}},
        exc=Exception('HTTP 400: {"error":{"type":"invalid_request_error","message":"bad request"}}'),
        provider_name="openrouter",
    )

    assert retry is False


def test_schema_fallback_does_not_retry_for_generic_openrouter_trace_id_error() -> None:
    retry = should_retry_without_response_schema(
        call_kwargs={"response_schema": {"type": "object"}},
        exc=Exception("HTTP 400 provider returned error trace_id=abc123"),
        provider_name="openrouter",
    )

    assert retry is False
