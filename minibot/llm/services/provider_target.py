from __future__ import annotations

from urllib.parse import urlparse


_BASE_URL_PROVIDER_ALIAS = {
    "openrouter.ai": "openrouter",
    "api.openrouter.ai": "openrouter",
    "openai.com": "openai",
    "api.openai.com": "openai",
    "x.ai": "xai",
    "api.x.ai": "xai",
}


def infer_provider_from_base_url(base_url: str | None) -> str | None:
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


def resolve_target_provider(*, provider_name: str, base_url: str | None) -> str:
    normalized_provider = provider_name.strip().lower()
    inferred_from_base_url = infer_provider_from_base_url(base_url)
    if normalized_provider == "openrouter":
        return "openrouter"
    if normalized_provider in {"openai", "openai_responses"}:
        if inferred_from_base_url is not None:
            return inferred_from_base_url
        return "openai"
    if inferred_from_base_url is not None:
        return inferred_from_base_url
    return normalized_provider
