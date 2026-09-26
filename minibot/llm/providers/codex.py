from __future__ import annotations

from typing import Any

from llm_async.models import Response
from llm_async_codex import CodexProvider

from minibot.llm.providers.openai_responses import PatchedOpenAIResponsesProvider


class PatchedCodexProvider(CodexProvider, PatchedOpenAIResponsesProvider):
    """Codex subscriptions reject stream=False; minibot's pipeline only calls the plain,
    non-streaming ``acomplete`` contract. Force streaming and drain it here so callers get a
    fully-populated ``Response`` like every other provider.

    Base order keeps MiniBot's native tool formatting while retaining Codex-specific behaviour."""

    async def acomplete(self, *args: Any, **kwargs: Any) -> Response:
        kwargs["stream"] = True
        response = await super().acomplete(*args, **kwargs)
        if response.stream_generator is not None:
            async for _ in response.stream_generator:
                pass
        return response
