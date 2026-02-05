from __future__ import annotations

from typing import Sequence

import logging

from minibot.core.memory import MemoryEntry
from llm_async.providers import ClaudeProvider, GoogleProvider, OpenAIProvider, OpenRouterProvider
from minibot.adapters.config.schema import LLMMConfig


LLM_PROVIDERS = {
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
    "google": GoogleProvider,
    "openrouter": OpenRouterProvider,
}


class LLMClient:
    def __init__(self, config: LLMMConfig) -> None:
        provider_cls = LLM_PROVIDERS.get(config.provider.lower(), OpenAIProvider)
        self._provider = provider_cls(api_key=config.api_key)
        self._model = config.model
        self._temperature = config.temperature
        self._max_new_tokens = config.max_new_tokens
        self._system_prompt = getattr(config, "system_prompt", "You are Minibot, a helpful assistant.")
        self._logger = logging.getLogger("minibot.llm")

    async def generate(self, history: Sequence[MemoryEntry], user_message: str) -> str:
        messages = [
            {"role": "system", "content": self._system_prompt},
        ]
        messages.extend(
            {"role": entry.role, "content": entry.content} for entry in history
        )
        messages.append({"role": "user", "content": user_message})

        if not self._provider.api_key:
            self._logger.warning("LLM provider key missing, falling back to echo", extra={"component": "llm"})
            return f"Echo: {user_message}"

        response = await self._provider.acomplete(
            self._model,
            messages,
            temperature=self._temperature,
            max_tokens=self._max_new_tokens,
        )

        if not response.main_response:
            raise RuntimeError("LLM did not return a completion")

        return response.main_response.content
