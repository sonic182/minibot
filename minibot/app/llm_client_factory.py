from __future__ import annotations

from minibot.adapters.config.schema import LLMMConfig, Settings
from minibot.core.agents import AgentSpec
from minibot.llm.provider_factory import LLMClient


class LLMClientFactory:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[tuple[str, str], LLMClient] = {}

    def create_default(self) -> LLMClient:
        config = self._resolved_config(self._settings.llm, provider_override=None)
        key = (config.provider, config.model)
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
        resolved = self._resolved_config(config, provider_override=config.provider)
        key = (resolved.provider, resolved.model)
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
        provider_name = config.provider.lower().strip()
        provider_cfg = self._settings.providers.get(provider_name)
        if provider_cfg is not None:
            if not config.api_key:
                config.api_key = provider_cfg.api_key
            if not config.base_url and provider_cfg.base_url:
                config.base_url = provider_cfg.base_url
        return config

