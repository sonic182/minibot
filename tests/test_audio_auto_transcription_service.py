from __future__ import annotations

from typing import Any

import pytest

from minibot.app.handlers.services.audio_transcription_service import (
    AudioAutoTranscribePolicy,
    AudioAutoTranscriptionService,
)
from minibot.core.channels import ChannelMessage
from minibot.llm.tools.base import ToolContext


class _Executor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def transcribe(
        self,
        *,
        path: str,
        context: ToolContext,
        language: str | None = None,
        task: str = "transcribe",
    ) -> dict[str, Any]:
        self.calls.append({"path": path, "context": context, "language": language, "task": task})
        if "fail" in path:
            return {"ok": False, "error": "boom", "path": path}
        return {"ok": True, "text": "turn on light", "path": path}


def _message(incoming_files: list[dict[str, Any]]) -> ChannelMessage:
    return ChannelMessage(
        channel="telegram",
        user_id=1,
        chat_id=1,
        message_id=1,
        text="",
        attachments=[],
        metadata={"incoming_files": incoming_files},
    )


@pytest.mark.asyncio
async def test_audio_auto_transcription_service_selects_short_audio_only() -> None:
    service = AudioAutoTranscriptionService(
        executor=_Executor(),
        policy=AudioAutoTranscribePolicy(enabled=True, max_duration_seconds=45),
    )
    result = await service.transcribe_incoming_audio(
        message=_message(
            [
                {
                    "path": "uploads/temp/voice_ok.ogg",
                    "filename": "voice_ok.ogg",
                    "mime": "audio/ogg",
                    "size_bytes": 10,
                    "source": "voice",
                    "duration_seconds": 12,
                },
                {
                    "path": "uploads/temp/voice_long.ogg",
                    "filename": "voice_long.ogg",
                    "mime": "audio/ogg",
                    "size_bytes": 10,
                    "source": "voice",
                    "duration_seconds": 120,
                },
            ]
        ),
        context=ToolContext(owner_id="1", channel="telegram", chat_id=1, user_id=1),
    )

    assert result.attempted_count == 1
    assert len(result.successes) == 1
    assert not result.errors
    assert result.successes[0].item.filename == "voice_ok.ogg"


@pytest.mark.asyncio
async def test_audio_auto_transcription_service_applies_prefix_with_errors() -> None:
    service = AudioAutoTranscriptionService(
        executor=_Executor(),
        policy=AudioAutoTranscribePolicy(enabled=True, max_duration_seconds=45),
    )
    result = await service.transcribe_incoming_audio(
        message=_message(
            [
                {
                    "path": "uploads/temp/voice_ok.ogg",
                    "filename": "voice_ok.ogg",
                    "mime": "audio/ogg",
                    "size_bytes": 10,
                    "source": "voice",
                    "duration_seconds": 12,
                },
                {
                    "path": "uploads/temp/voice_fail.ogg",
                    "filename": "voice_fail.ogg",
                    "mime": "audio/ogg",
                    "size_bytes": 10,
                    "source": "voice",
                    "duration_seconds": 8,
                },
            ]
        ),
        context=ToolContext(owner_id="1", channel="telegram", chat_id=1, user_id=1),
    )

    updated = service.apply_to_model_text("hola", result)
    assert "Automatic audio transcriptions from incoming files:" in updated
    assert "turn on light" in updated
    assert "Automatic transcription errors:" in updated
    assert "voice_fail.ogg: boom" in updated
