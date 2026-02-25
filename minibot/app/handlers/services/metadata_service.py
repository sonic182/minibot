from __future__ import annotations

from typing import Any

from minibot.llm.provider_factory import LLMClient


class ResponseMetadataService:
    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    def provider_name(self) -> str | None:
        provider_getter = getattr(self._llm_client, "provider_name", None)
        if callable(provider_getter):
            provider = provider_getter()
            if isinstance(provider, str) and provider:
                return provider
        return None

    def model_name(self) -> str | None:
        model_getter = getattr(self._llm_client, "model_name", None)
        if callable(model_getter):
            model = model_getter()
            if isinstance(model, str) and model:
                return model
        return None

    def response_metadata(self, should_reply: bool) -> dict[str, Any]:
        return {
            "should_reply": should_reply,
            "llm_provider": self.provider_name(),
            "llm_model": self.model_name(),
        }
