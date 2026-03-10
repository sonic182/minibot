from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from logging import Logger
from typing import Any
from urllib.parse import urlparse

import aiosonic

from minibot.adapters.config.schema import Settings
from minibot.core.agents import AgentSpec

_MODELS_API_URL = "https://models.dev/api.json"
_REQUEST_TIMEOUT_SECONDS = 20
_BASE_URL_PROVIDER_ALIAS = {
    "openrouter.ai": "openrouter",
    "api.openrouter.ai": "openrouter",
    "openai.com": "openai",
    "api.openai.com": "openai",
    "x.ai": "xai",
    "api.x.ai": "xai",
}


async def apply_runtime_token_autoconfig_async(
    *,
    settings: Settings,
    agent_specs: list[AgentSpec],
    logger: Logger,
) -> list[AgentSpec]:
    payload = await _fetch_models_catalog(logger)
    if payload is None:
        return agent_specs

    ratio = settings.memory.context_ratio_before_compact
    main_provider = settings.llm.provider
    main_model = settings.llm.model
    main_base_url = _effective_base_url(settings, provider_name=main_provider)
    main_limits = _resolve_limits(
        payload=payload,
        provider_name=main_provider,
        model_name=main_model,
        base_url=main_base_url,
    )
    if main_limits is not None:
        derived_budget = max(1, int(main_limits["context"] * ratio))
        derived_max_new_tokens = max(1, min(main_limits["output"], derived_budget))
        previous_history = settings.memory.max_history_tokens
        previous_llm_max = settings.llm.max_new_tokens
        settings.memory.max_history_tokens = derived_budget
        settings.llm.max_new_tokens = derived_max_new_tokens
        logger.info(
            "token auto-config applied for main model",
            extra={
                "component": "startup",
                "provider": main_provider,
                "model": main_model,
                "catalog_provider": main_limits["catalog_provider"],
                "context_limit": main_limits["context"],
                "output_limit": main_limits["output"],
                "ratio": ratio,
                "memory_max_history_tokens_before": previous_history,
                "memory_max_history_tokens_after": derived_budget,
                "llm_max_new_tokens_before": previous_llm_max,
                "llm_max_new_tokens_after": derived_max_new_tokens,
            },
        )
    else:
        logger.warning(
            "token auto-config skipped for main model; keeping configured values",
            extra={"component": "startup", "provider": main_provider, "model": main_model},
        )

    adjusted_specs: list[AgentSpec] = []
    for spec in agent_specs:
        provider_name = spec.model_provider or settings.llm.provider
        model_name = spec.model or settings.llm.model
        base_url = _effective_base_url(settings, provider_name=provider_name)
        limits = _resolve_limits(
            payload=payload,
            provider_name=provider_name,
            model_name=model_name,
            base_url=base_url,
        )
        if limits is None:
            adjusted_specs.append(spec)
            continue
        derived_budget = max(1, int(limits["context"] * ratio))
        derived_max_new_tokens = max(1, min(limits["output"], derived_budget))
        logger.info(
            "token auto-config applied for agent model",
            extra={
                "component": "startup",
                "agent_name": spec.name,
                "provider": provider_name,
                "model": model_name,
                "catalog_provider": limits["catalog_provider"],
                "context_limit": limits["context"],
                "output_limit": limits["output"],
                "ratio": ratio,
                "agent_max_new_tokens_before": spec.max_new_tokens,
                "agent_max_new_tokens_after": derived_max_new_tokens,
            },
        )
        adjusted_specs.append(replace(spec, max_new_tokens=derived_max_new_tokens))
    return adjusted_specs


def apply_runtime_token_autoconfig(
    *,
    settings: Settings,
    agent_specs: list[AgentSpec],
    logger: Logger,
) -> list[AgentSpec]:
    return asyncio.run(
        apply_runtime_token_autoconfig_async(
            settings=settings,
            agent_specs=agent_specs,
            logger=logger,
        )
    )


async def _fetch_models_catalog(logger: Logger) -> dict[str, Any] | None:
    try:
        payload = await _fetch_models_catalog_async()
        if not isinstance(payload, dict):
            logger.warning(
                "token auto-config skipped: unexpected models catalog payload type",
                extra={"component": "startup", "url": _MODELS_API_URL},
            )
            return None
        return payload
    except Exception as exc:
        logger.warning(
            "token auto-config skipped: failed to fetch models catalog",
            extra={"component": "startup", "url": _MODELS_API_URL, "error": str(exc)},
        )
        return None


async def _fetch_models_catalog_async() -> object:
    client = aiosonic.HTTPClient()
    response = await asyncio.wait_for(
        client.get(
            _MODELS_API_URL,
            headers={
                "User-Agent": "minibot-startup-token-autoconfig/1.0",
                "Accept": "application/json",
            },
        ),
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    body = await asyncio.wait_for(response.content(), timeout=_REQUEST_TIMEOUT_SECONDS)
    return json.loads(body.decode("utf-8"))


def _resolve_limits(
    *,
    payload: dict[str, Any],
    provider_name: str,
    model_name: str,
    base_url: str | None,
) -> dict[str, Any] | None:
    target_provider = _catalog_provider_key(provider_name=provider_name, base_url=base_url)
    model_candidates = _candidate_model_ids(model_name=model_name, target_provider=target_provider)

    provider_hits = _hits_for_provider(
        payload=payload,
        provider_key=target_provider,
        model_candidates=model_candidates,
    )
    if provider_hits:
        context_limit = min(hit["context"] for hit in provider_hits)
        output_limit = min(hit["output"] for hit in provider_hits)
        return {"catalog_provider": target_provider, "context": context_limit, "output": output_limit}
    return None


def _catalog_provider_key(*, provider_name: str, base_url: str | None) -> str:
    normalized_provider = provider_name.strip().lower()
    inferred_from_base_url = _infer_provider_from_base_url(base_url)
    if normalized_provider == "openrouter":
        return "openrouter"
    if normalized_provider in {"openai", "openai_responses"}:
        if inferred_from_base_url is not None:
            return inferred_from_base_url
        return "openai"
    if inferred_from_base_url is not None:
        return inferred_from_base_url
    return normalized_provider


def _infer_provider_from_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return None
    for suffix, provider_key in _BASE_URL_PROVIDER_ALIAS.items():
        if host == suffix or host.endswith(f".{suffix}"):
            return provider_key
    return None


def _candidate_model_ids(*, model_name: str, target_provider: str) -> list[str]:
    normalized_model = model_name.strip()
    candidates: list[str] = []

    def _add(value: str) -> None:
        trimmed = value.strip()
        if trimmed and trimmed not in candidates:
            candidates.append(trimmed)

    _add(normalized_model)
    if "/" in normalized_model:
        _add(normalized_model.split("/", 1)[1])
    else:
        if target_provider in {"openai", "xai"}:
            _add(f"{target_provider}/{normalized_model}")
    return candidates


def _hits_for_provider(
    *,
    payload: dict[str, Any],
    provider_key: str,
    model_candidates: list[str],
) -> list[dict[str, int]]:
    provider_payload = payload.get(provider_key)
    if not isinstance(provider_payload, dict):
        return []
    models = provider_payload.get("models")
    if not isinstance(models, dict):
        return []
    hits: list[dict[str, int]] = []
    for model_id in model_candidates:
        model = models.get(model_id)
        parsed = _parse_model_limits(model)
        if parsed is not None:
            hits.append(parsed)
    return hits


def _parse_model_limits(model_payload: object) -> dict[str, int] | None:
    if not isinstance(model_payload, dict):
        return None
    limit = model_payload.get("limit")
    if not isinstance(limit, dict):
        return None
    context = limit.get("context")
    output = limit.get("output")
    if not isinstance(context, int) or not isinstance(output, int):
        return None
    if context <= 0 or output <= 0:
        return None
    return {"context": context, "output": output}


def _effective_base_url(settings: Settings, *, provider_name: str) -> str | None:
    normalized = provider_name.strip().lower()
    provider_cfg = settings.providers.get(normalized)
    if provider_cfg is not None and provider_cfg.base_url:
        return provider_cfg.base_url
    if settings.llm.provider.strip().lower() == normalized and settings.llm.base_url:
        return settings.llm.base_url
    return None
