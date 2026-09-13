from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from llm_async.models import Response
from llm_async_codex import CodexProvider

from minibot.llm.providers.openai_responses import PatchedOpenAIResponsesProvider

# The /models endpoint gates which models it returns by this query param, compared as a version
# string against each model's `minimal_client_version` (verified empirically: low values return
# an empty or partial list). Track the current Codex CLI release so new models stay unlocked as
# their floor rises; bump this if a model goes missing from get_model_capabilities().
_MODELS_CLIENT_VERSION = "0.154.0"


@dataclass(frozen=True, slots=True)
class CodexModelCapabilities:
    """Fields we actually use from a `/models` entry; the real payload has many more."""

    slug: str
    context_window: int
    max_context_window: int
    auto_compact_token_limit: int | None


class PatchedCodexProvider(CodexProvider, PatchedOpenAIResponsesProvider):
    """Codex subscriptions reject stream=False; minibot's pipeline only calls the plain,
    non-streaming ``acomplete`` contract. Force streaming and drain it here so callers get a
    fully-populated ``Response`` like every other provider.

    Base order keeps MiniBot's native tool formatting while retaining Codex-specific behaviour."""

    _models_cache: list[dict[str, Any]] | None = None

    async def acomplete(self, *args: Any, **kwargs: Any) -> Response:
        kwargs["stream"] = True
        # The Codex backend rejects this Responses API parameter outright (HTTP 400), unlike
        # regular OpenAI Responses endpoints.
        kwargs.pop("max_output_tokens", None)
        response = await super().acomplete(*args, **kwargs)
        if response.stream_generator is not None:
            async for _ in response.stream_generator:
                pass
        return response

    async def _ensure_models_cache(self) -> list[dict[str, Any]]:
        """Fetch (and cache) `GET /models`.

        Bypasses `_single_complete`, the only place the automatic token-refresh check normally
        runs, so it's called here explicitly — a stale token would otherwise 401 on this path.
        """
        await self._ensure_fresh_credentials()
        if self._models_cache is None:
            payload = await self.request("GET", f"/models?client_version={_MODELS_CLIENT_VERSION}")
            models = payload.get("models") if isinstance(payload, dict) else None
            self._models_cache = [entry for entry in models if isinstance(entry, dict)] if models else []
        return self._models_cache

    async def get_model_capabilities(self, model: str) -> CodexModelCapabilities | None:
        """Return the `/models` entry for `model`, or None if it's not in the catalog."""
        for entry in await self._ensure_models_cache():
            if entry.get("slug") != model:
                continue
            context_window = entry.get("context_window")
            max_context_window = entry.get("max_context_window")
            if not isinstance(context_window, int) or not isinstance(max_context_window, int):
                return None
            auto_compact = entry.get("auto_compact_token_limit")
            return CodexModelCapabilities(
                slug=model,
                context_window=context_window,
                max_context_window=max_context_window,
                auto_compact_token_limit=auto_compact if isinstance(auto_compact, int) else None,
            )
        return None

    async def list_model_slugs(self) -> list[str]:
        """Return the available model slugs, for interactive model selection."""
        return sorted({entry["slug"] for entry in await self._ensure_models_cache() if entry.get("slug")})
