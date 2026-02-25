from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class RequestContext:
    model: str
    provider_name: str
    is_responses_provider: bool
    temperature: float | None
    max_new_tokens: int | None
    prompt_cache_enabled: bool
    prompt_cache_retention: str | None
    reasoning_effort: str | None
    openrouter_models: tuple[str, ...]
    openrouter_provider: dict[str, Any]
    openrouter_reasoning_enabled: bool | None
    openrouter_plugins: tuple[dict[str, Any], ...]


def build_messages(
    *,
    history: Sequence[Any],
    user_message: str,
    user_content: str | list[dict[str, Any]] | None,
    system_prompt: str,
) -> list[dict[str, Any]]:
    messages = [
        {"role": "system", "content": system_prompt},
    ]
    messages.extend({"role": entry.role, "content": entry.content} for entry in history)
    final_user_content: str | list[dict[str, Any]] = user_message
    if user_content is not None:
        final_user_content = user_content
    messages.append({"role": "user", "content": final_user_content})
    return messages


def build_generate_extra_kwargs(
    *,
    ctx: RequestContext,
    prompt_cache_key: str | None,
    previous_response_id: str | None,
    system_prompt: str,
) -> dict[str, Any]:
    extra_kwargs: dict[str, Any] = {}
    if prompt_cache_key and ctx.is_responses_provider and ctx.prompt_cache_enabled:
        extra_kwargs["prompt_cache_key"] = prompt_cache_key
    if previous_response_id and ctx.is_responses_provider:
        extra_kwargs["previous_response_id"] = previous_response_id
    if ctx.is_responses_provider and ctx.prompt_cache_enabled and ctx.prompt_cache_retention:
        extra_kwargs["prompt_cache_retention"] = ctx.prompt_cache_retention
    if ctx.is_responses_provider and ctx.reasoning_effort:
        extra_kwargs.setdefault("reasoning", {"effort": ctx.reasoning_effort})
    if ctx.is_responses_provider:
        extra_kwargs["instructions"] = system_prompt
    return extra_kwargs


def build_generate_step_call_kwargs(
    *,
    ctx: RequestContext,
    conversation: Sequence[dict[str, Any]],
    tool_specs: Sequence[Any] | None,
    strict_response_schema: dict[str, Any] | None,
    extra_kwargs: dict[str, Any],
) -> dict[str, Any]:
    call_kwargs: dict[str, Any] = {
        "model": ctx.model,
        "messages": list(conversation),
        "tools": tool_specs,
        "response_schema": strict_response_schema,
    }
    if ctx.temperature is not None:
        call_kwargs["temperature"] = ctx.temperature
    if ctx.is_responses_provider:
        if ctx.max_new_tokens is not None:
            call_kwargs["max_output_tokens"] = ctx.max_new_tokens
    else:
        resolved_max_tokens = resolved_max_tokens_for_request(ctx)
        if resolved_max_tokens is not None:
            call_kwargs["max_tokens"] = resolved_max_tokens
    call_kwargs.update(openrouter_kwargs(ctx))
    call_kwargs.update(extra_kwargs)
    return call_kwargs


def build_complete_once_call_kwargs(
    *,
    ctx: RequestContext,
    messages: Sequence[dict[str, Any]],
    tool_specs: Sequence[Any] | None,
    strict_response_schema: dict[str, Any] | None,
    prompt_cache_key: str | None,
    previous_response_id: str | None,
) -> dict[str, Any]:
    call_kwargs: dict[str, Any] = {
        "model": ctx.model,
        "messages": list(messages),
        "tools": tool_specs,
        "response_schema": strict_response_schema,
    }
    if ctx.temperature is not None:
        call_kwargs["temperature"] = ctx.temperature
    if ctx.is_responses_provider:
        if ctx.max_new_tokens is not None:
            call_kwargs["max_output_tokens"] = ctx.max_new_tokens
        instructions = extract_system_instructions(messages)
        if instructions:
            call_kwargs["instructions"] = instructions
    else:
        resolved_max_tokens = resolved_max_tokens_for_request(ctx)
        if resolved_max_tokens is not None:
            call_kwargs["max_tokens"] = resolved_max_tokens
    call_kwargs.update(openrouter_kwargs(ctx))
    if prompt_cache_key and ctx.is_responses_provider and ctx.prompt_cache_enabled:
        call_kwargs["prompt_cache_key"] = prompt_cache_key
    if previous_response_id and ctx.is_responses_provider:
        call_kwargs["previous_response_id"] = previous_response_id
    if ctx.is_responses_provider and ctx.prompt_cache_enabled and ctx.prompt_cache_retention:
        call_kwargs["prompt_cache_retention"] = ctx.prompt_cache_retention
    if ctx.is_responses_provider and ctx.reasoning_effort:
        call_kwargs.setdefault("reasoning", {"effort": ctx.reasoning_effort})
    return call_kwargs


def build_continue_call_kwargs(
    *,
    ctx: RequestContext,
    previous_response_id: str,
    prompt_cache_key: str | None,
    system_prompt: str,
    response_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    call_kwargs: dict[str, Any] = {
        "model": ctx.model,
        "messages": [{"role": "user", "content": "Continue exactly where you left off. Do not repeat."}],
        "tools": None,
        "response_schema": response_schema,
        "previous_response_id": previous_response_id,
        "instructions": system_prompt,
    }
    if ctx.temperature is not None:
        call_kwargs["temperature"] = ctx.temperature
    if ctx.max_new_tokens is not None:
        call_kwargs["max_output_tokens"] = ctx.max_new_tokens
    if prompt_cache_key and ctx.prompt_cache_enabled:
        call_kwargs["prompt_cache_key"] = prompt_cache_key
    if ctx.prompt_cache_enabled and ctx.prompt_cache_retention:
        call_kwargs["prompt_cache_retention"] = ctx.prompt_cache_retention
    if ctx.reasoning_effort:
        call_kwargs.setdefault("reasoning", {"effort": ctx.reasoning_effort})
    return call_kwargs


def extract_system_instructions(messages: Sequence[dict[str, Any]]) -> str | None:
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "system":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def openrouter_kwargs(ctx: RequestContext) -> dict[str, Any]:
    if ctx.provider_name != "openrouter":
        return {}
    kwargs: dict[str, Any] = {}
    if ctx.openrouter_models:
        kwargs["models"] = list(ctx.openrouter_models)
    if ctx.openrouter_provider:
        kwargs["provider"] = dict(ctx.openrouter_provider)
    if ctx.openrouter_reasoning_enabled is True:
        kwargs["reasoning"] = {"enabled": True}
    if ctx.openrouter_plugins:
        kwargs["plugins"] = list(ctx.openrouter_plugins)
    return kwargs


def resolved_max_tokens_for_request(ctx: RequestContext) -> int | None:
    if ctx.is_responses_provider:
        return None
    if ctx.provider_name == "openrouter":
        if ctx.max_new_tokens is None:
            return 4096
        return min(ctx.max_new_tokens, 32768)
    if ctx.max_new_tokens is not None:
        return ctx.max_new_tokens
    return None
