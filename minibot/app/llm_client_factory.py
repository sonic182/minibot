from __future__ import annotations

import json

from minibot.adapters.config.schema import LLMMConfig, OpenRouterProviderRoutingConfig, Settings
from minibot.core.agents import AgentSpec
from minibot.llm.provider_factory import LLMClient


class LLMClientFactory:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[tuple[object, ...], LLMClient] = {}

    def create_default(self) -> LLMClient:
        config = self._resolved_config(self._settings.llm, provider_override=None)
        key = self._cache_key(config)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        client = LLMClient(config)
        self._cache[key] = client
        return client

    def create_for_agent(self, spec: AgentSpec) -> LLMClient:
        config = self._settings.llm.model_copy(deep=True)
        if spec.model_provider:
            config.provider = spec.model_provider
        if spec.model:
            config.model = spec.model
        if spec.temperature is not None:
            config.temperature = spec.temperature
        if spec.max_new_tokens is not None:
            config.max_new_tokens = spec.max_new_tokens
        if spec.reasoning_effort is not None:
            config.reasoning_effort = spec.reasoning_effort
        if spec.max_tool_iterations is not None:
            config.max_tool_iterations = spec.max_tool_iterations
        if spec.openrouter_provider_overrides:
            provider_cfg = config.openrouter.provider
            if provider_cfg is None:
                provider_cfg = OpenRouterProviderRoutingConfig()
            merged_provider_cfg = {
                **provider_cfg.model_dump(mode="python", exclude_none=True),
                **spec.openrouter_provider_overrides,
            }
            provider_cfg = OpenRouterProviderRoutingConfig.model_validate(merged_provider_cfg)
            config.openrouter.provider = provider_cfg
        resolved = self._resolved_config(config, provider_override=config.provider)
        key = self._cache_key(resolved)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        client = LLMClient(resolved)
        self._cache[key] = client
        return client

    def _resolved_config(self, base: LLMMConfig, provider_override: str | None) -> LLMMConfig:
        config = base.model_copy(deep=True)
        if provider_override:
            config.provider = provider_override
            config.api_key = ""
            config.base_url = None
        provider_name = config.provider.lower().strip()
        provider_cfg = self._settings.providers.get(provider_name)
        if provider_cfg is not None:
            config.api_key = provider_cfg.api_key
            config.base_url = provider_cfg.base_url
        return config

    @staticmethod
    def _cache_key(config: LLMMConfig) -> tuple[object, ...]:
        openrouter_provider_payload = None
        if config.openrouter.provider is not None:
            openrouter_provider_payload = json.dumps(
                config.openrouter.provider.model_dump(mode="python"),
                sort_keys=True,
                separators=(",", ":"),
            )
        return (
            config.provider.lower().strip(),
            config.model,
            config.temperature,
            config.max_new_tokens,
            config.reasoning_effort,
            config.max_tool_iterations,
            config.request_timeout_seconds,
            config.sock_connect_timeout_seconds,
            config.sock_read_timeout_seconds,
            config.retry_attempts,
            config.retry_delay_seconds,
            config.api_key,
            config.base_url,
            openrouter_provider_payload,
        )
