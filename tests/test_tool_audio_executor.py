from __future__ import annotations

from typing import Any

import pytest
from llm_async.models import Tool

from minibot.app.handlers.services.tool_audio_executor import ToolBindingAudioTranscriptionExecutor
from minibot.llm.tools.base import ToolBinding, ToolContext


@pytest.mark.asyncio
async def test_tool_audio_executor_returns_dict_payload() -> None:
    async def _handler(payload: dict[str, Any], _context: ToolContext) -> dict[str, Any]:
        return {"ok": True, "path": payload["path"], "text": "ok"}

    binding = ToolBinding(
        tool=Tool(name="transcribe_audio", description="", parameters={"type": "object"}),
        handler=_handler,
    )
    executor = ToolBindingAudioTranscriptionExecutor(binding)

    result = await executor.transcribe(
        path="uploads/temp/voice.ogg",
        context=ToolContext(owner_id="1"),
        task="transcribe",
    )

    assert result["ok"] is True
    assert result["path"] == "uploads/temp/voice.ogg"


@pytest.mark.asyncio
async def test_tool_audio_executor_rejects_non_dict_result() -> None:
    async def _handler(_payload: dict[str, Any], _context: ToolContext) -> Any:
        return "invalid"

    binding = ToolBinding(
        tool=Tool(name="transcribe_audio", description="", parameters={"type": "object"}),
        handler=_handler,
    )
    executor = ToolBindingAudioTranscriptionExecutor(binding)

    result = await executor.transcribe(
        path="uploads/temp/voice.ogg",
        context=ToolContext(owner_id="1"),
        task="transcribe",
    )

    assert result["ok"] is False
    assert "invalid transcription result type" in result["error"]
