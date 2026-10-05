from __future__ import annotations

from typing import Any

from llm_async.models import Response
from llm_async_codex import CodexProvider

from minibot.config.schema import CodexConfig
from minibot.llm.providers.openai_responses import PatchedOpenAIResponsesProvider


class PatchedCodexProvider(CodexProvider, PatchedOpenAIResponsesProvider):
    """Codex subscriptions reject stream=False; minibot's pipeline only calls the plain,
    non-streaming ``acomplete`` contract. Force streaming and drain it here so callers get a
    fully-populated ``Response`` like every other provider.

    Base order keeps MiniBot's native tool formatting while retaining Codex-specific behaviour.

    The model list is requested with ``models_client_version`` (``[codex] version``) rather than the
    version ``llm-async-codex`` hardcodes, because the endpoint hides models newer than the client version."""

    _stream_responses_request = PatchedOpenAIResponsesProvider._stream_responses_request

    def __init__(self, *args: Any, models_client_version: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._models_client_version = models_client_version or CodexConfig().version

    async def _ensure_models_cache(self) -> list[dict[str, Any]]:
        await self._ensure_fresh_credentials()
        if self._models_cache is None:
            payload = await self.request("GET", f"/models?client_version={self._models_client_version}")
            models = payload.get("models") if isinstance(payload, dict) else None
            entries = models if isinstance(models, list) else []
            self._models_cache = [entry for entry in entries if isinstance(entry, dict)]
        return self._models_cache

    async def acomplete(self, *args: Any, **kwargs: Any) -> Response:
        kwargs["stream"] = True
        response = await super().acomplete(*args, **kwargs)
        if response.stream_generator is not None:
            async for _ in response.stream_generator:
                pass
        return response
