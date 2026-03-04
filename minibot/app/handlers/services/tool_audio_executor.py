from __future__ import annotations

from typing import Any, Protocol

from minibot.llm.tools.base import ToolBinding, ToolContext


class AudioTranscriptionExecutor(Protocol):
    async def transcribe(
        self,
        *,
        path: str,
        context: ToolContext,
        language: str | None = None,
        task: str = "transcribe",
    ) -> dict[str, Any]: ...


class ToolBindingAudioTranscriptionExecutor:
    def __init__(self, binding: ToolBinding) -> None:
        self._binding = binding

    async def transcribe(
        self,
        *,
        path: str,
        context: ToolContext,
        language: str | None = None,
        task: str = "transcribe",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": path, "task": task}
        if language:
            payload["language"] = language
        result = await self._binding.handler(payload, context)
        if isinstance(result, dict):
            return result
        return {"ok": False, "path": path, "error": "invalid transcription result type"}
