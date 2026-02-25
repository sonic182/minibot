from __future__ import annotations

from typing import Any, Awaitable, Callable

from minibot.llm.services.models import LLMCompaction, LLMGeneration
from minibot.llm.services.request_builder import RequestContext, build_continue_call_kwargs
from minibot.llm.services.usage_parser import (
    extract_response_id,
    extract_total_tokens_from_payload,
    extract_usage_from_response,
)
from minibot.shared.retries import AsyncRetriesService, RetryPolicy


async def compact_response(
    *,
    provider: Any,
    is_responses_provider: bool,
    model: str,
    prompt_cache_enabled: bool,
    previous_response_id: str,
    prompt_cache_key: str | None,
    retries_service: AsyncRetriesService,
    retry_attempts: int,
    retry_base_delay_seconds: float,
    retry_max_delay_seconds: float,
    logger: Any,
) -> LLMCompaction:
    if not is_responses_provider:
        raise RuntimeError("compaction endpoint is available only for openai_responses provider")
    payload: dict[str, Any] = {
        "model": model,
        "previous_response_id": previous_response_id,
    }
    if prompt_cache_key and prompt_cache_enabled:
        payload["prompt_cache_key"] = prompt_cache_key

    retry_policy = RetryPolicy(
        max_attempts=retry_attempts,
        base_delay_seconds=retry_base_delay_seconds,
        max_delay_seconds=retry_max_delay_seconds,
        backoff_factor=2.0,
        jitter=False,
        retry_exceptions=(Exception,),
    )

    def on_retry(exc: Exception, attempt: int, delay: float) -> None:
        logger.warning(
            "responses compact request failed; retrying",
            extra={
                "attempt": attempt,
                "next_delay_seconds": round(delay, 3),
                "model": model,
                "error": str(exc),
            },
        )

    raw = await retries_service.run(
        lambda: provider.request("POST", "/responses/compact", json_data=payload),
        policy=retry_policy,
        on_retry=on_retry,
    )
    response_id = raw.get("id") if isinstance(raw.get("id"), str) else ""
    if not response_id:
        raise RuntimeError("responses compact endpoint returned payload without id")
    output = raw.get("output")
    normalized_output = output if isinstance(output, list) else []
    total_tokens = extract_total_tokens_from_payload(raw)
    return LLMCompaction(
        response_id=response_id,
        output=normalized_output,
        total_tokens=total_tokens,
    )


async def continue_incomplete_response(
    *,
    complete_with_schema_fallback: Callable[[dict[str, Any]], Awaitable[Any]],
    ctx: RequestContext,
    previous_response_id: str,
    prompt_cache_key: str | None,
    system_prompt: str,
    response_schema: dict[str, Any] | None,
    logger: Any,
) -> LLMGeneration:
    call_kwargs = build_continue_call_kwargs(
        ctx=ctx,
        previous_response_id=previous_response_id,
        prompt_cache_key=prompt_cache_key,
        system_prompt=system_prompt,
        response_schema=response_schema,
    )
    logger.warning(
        "responses output incomplete; attempting one continuation",
        extra={"model": ctx.model, "response_id": previous_response_id},
    )
    response = await complete_with_schema_fallback(call_kwargs)
    message = response.main_response
    if not message:
        raise RuntimeError("LLM did not return a continuation completion")
    usage = extract_usage_from_response(response)
    return LLMGeneration(
        payload=message.content,
        response_id=extract_response_id(response),
        total_tokens=usage.total_tokens,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        reasoning_output_tokens=usage.reasoning_output_tokens,
        status=usage.status,
        incomplete_reason=usage.incomplete_reason,
    )
