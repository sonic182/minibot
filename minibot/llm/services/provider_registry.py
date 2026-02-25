from __future__ import annotations

from typing import Any

from llm_async.providers import ClaudeProvider, GoogleProvider, OpenAIProvider, OpenRouterProvider
from llm_async.providers.openai_responses import OpenAIResponsesProvider


LLM_PROVIDERS = {
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
    "google": GoogleProvider,
    "openrouter": OpenRouterProvider,
    "openai_responses": OpenAIResponsesProvider,
}


def resolve_provider_class(configured_provider: str) -> tuple[type[Any], str]:
    provider_cls = LLM_PROVIDERS.get(configured_provider, OpenAIProvider)
    provider_name = configured_provider if configured_provider in LLM_PROVIDERS else "openai"
    return provider_cls, provider_name


def is_responses_provider_instance(provider: Any) -> bool:
    return isinstance(provider, OpenAIResponsesProvider)
