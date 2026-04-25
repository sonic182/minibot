from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiosonic
from llm_async.utils.retry import RetryConfig

from minibot.adapters.config.schema import LLMMConfig
from minibot.llm.services.provider_registry import resolve_provider_class


def create_provider(config: LLMMConfig) -> tuple[Any, str]:
    configured_provider = config.provider.lower()
    provider_cls, provider_name = resolve_provider_class(configured_provider)
    normalized_base_url = config.base_url.rstrip("/") if config.base_url else None

    timeouts = aiosonic.Timeouts(
        sock_connect=float(config.sock_connect_timeout_seconds),
        sock_read=float(config.sock_read_timeout_seconds),
        request_timeout=float(config.request_timeout_seconds),
    )
    connector = aiosonic.TCPConnector(timeouts=timeouts)
    retry_config = RetryConfig(
        max_attempts=config.retry_attempts + 1,
        base_delay=float(config.retry_delay_seconds),
        max_delay=float(config.retry_delay_seconds),
        backoff_factor=1.0,
        jitter=False,
    )
    effective_http2 = config.http2
    if normalized_base_url:
        parsed_base_url = urlparse(normalized_base_url)
        if parsed_base_url.scheme.lower() == "http":
            effective_http2 = False
    provider_kwargs: dict[str, Any] = {
        "api_key": config.api_key,
        "retry_config": retry_config,
        "client_kwargs": {"connector": connector},
        "http2": effective_http2,
    }
    if normalized_base_url:
        provider_kwargs["base_url"] = normalized_base_url

    return provider_cls(**provider_kwargs), provider_name


def load_system_prompt(config: LLMMConfig) -> str:
    prompt_file = getattr(config, "system_prompt_file", None)
    if prompt_file is not None:
        normalized = prompt_file.strip() if isinstance(prompt_file, str) else None
        if normalized:
            prompt_path = Path(normalized)
            if not prompt_path.exists():
                raise FileNotFoundError(f"system_prompt_file configured but file not found: {normalized}")
            if not prompt_path.is_file():
                raise ValueError(f"system_prompt_file configured but path is not a file: {normalized}")
            content = prompt_path.read_text(encoding="utf-8").strip()
            if not content:
                raise ValueError(f"system_prompt_file configured but file is empty: {normalized}")
            return content
    return getattr(config, "system_prompt", "You are Minibot, a helpful assistant.")


def build_openrouter_provider_payload(config: LLMMConfig) -> dict[str, Any]:
    provider_cfg = getattr(getattr(config, "openrouter", None), "provider", None)
    if provider_cfg is None:
        return {}

    payload: dict[str, Any] = dict(getattr(provider_cfg, "provider_extra", {}) or {})
    typed_fields = (
        "order",
        "allow_fallbacks",
        "require_parameters",
        "data_collection",
        "zdr",
        "enforce_distillable_text",
        "only",
        "ignore",
        "quantizations",
        "sort",
        "preferred_min_throughput",
        "preferred_max_latency",
        "max_price",
    )
    for field_name in typed_fields:
        value = getattr(provider_cfg, field_name, None)
        if value is not None:
            payload[field_name] = value
    return payload


def resolve_openrouter_reasoning_enabled(config: LLMMConfig) -> bool | None:
    openrouter_cfg = getattr(config, "openrouter", None)
    if openrouter_cfg is None:
        return None
    value = getattr(openrouter_cfg, "reasoning_enabled", None)
    if isinstance(value, bool):
        return value
    return None
