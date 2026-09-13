from __future__ import annotations

from typing import Any

from llm_async.models import Response
from llm_async_codex import CodexProvider


class PatchedCodexProvider(CodexProvider):
    """Codex subscriptions reject stream=False; minibot's pipeline only calls the plain,
    non-streaming ``acomplete`` contract. Force streaming and drain it here so callers get a
    fully-populated ``Response`` like every other provider."""

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
