from __future__ import annotations

from typing import Any

from minibot.llm.services.structured_output_policy import (
    apply_structured_output_prompt,
    normalize_structured_output_mode,
    should_retry_without_response_schema,
)


async def complete_with_schema_fallback(
    *,
    provider: Any,
    call_kwargs: dict[str, Any],
    model: str,
    provider_display_name: str,
    logger: Any,
    structured_output_mode: str | None = None,
) -> Any:
    mode = normalize_structured_output_mode(structured_output_mode)
    provider_call_kwargs = _provider_call_kwargs(call_kwargs)
    try:
        return await provider.acomplete(**provider_call_kwargs)
    except Exception as exc:
        if not should_retry_without_response_schema(call_kwargs=provider_call_kwargs, exc=exc, mode=mode):
            raise
        prompt_schema = call_kwargs.get(
            "_structured_output_prompt_schema",
            provider_call_kwargs.get("response_schema"),
        )
        retry_kwargs = apply_structured_output_prompt(provider_call_kwargs, prompt_schema)
        retry_kwargs["response_schema"] = None
        logger.warning(
            "retrying structured request without response_schema",
            extra={"model": model, "provider": provider_display_name, "structured_output_mode": mode},
        )
        return await provider.acomplete(**retry_kwargs)


def _provider_call_kwargs(call_kwargs: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in call_kwargs.items() if key != "_structured_output_prompt_schema"}
