from __future__ import annotations

from typing import Any


async def complete_with_schema_fallback(
    *,
    provider: Any,
    call_kwargs: dict[str, Any],
    provider_name: str,
    model: str,
    provider_display_name: str,
    logger: Any,
) -> Any:
    try:
        return await provider.acomplete(**call_kwargs)
    except Exception as exc:
        if not should_retry_without_response_schema(call_kwargs=call_kwargs, exc=exc, provider_name=provider_name):
            raise
        retry_kwargs = dict(call_kwargs)
        retry_kwargs["response_schema"] = None
        logger.warning(
            "retrying openrouter request without response_schema",
            extra={"model": model, "provider": provider_display_name},
        )
        return await provider.acomplete(**retry_kwargs)


def should_retry_without_response_schema(*, call_kwargs: dict[str, Any], exc: Exception, provider_name: str) -> bool:
    if provider_name != "openrouter":
        return False
    if call_kwargs.get("response_schema") is None:
        return False
    message = str(exc).lower()
    if "json mode is not supported" in message:
        return True
    if '"code":20024' in message:
        return True
    return False
