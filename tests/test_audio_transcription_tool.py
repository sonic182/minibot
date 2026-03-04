from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from minibot.adapters.config.schema import AudioTranscriptionToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.llm.tools.audio_transcription import AudioTranscriptionTool
from minibot.llm.tools.base import ToolContext


@dataclass
class _Segment:
    start: float
    end: float
    text: str


@dataclass
class _Info:
    language: str
    language_probability: float
    duration: float


@pytest.mark.asyncio
async def test_audio_transcription_tool_transcribes_with_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    audio_file = tmp_path / "uploads" / "hello.wav"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"fake-audio")
    captured_init: dict[str, Any] = {}
    captured_transcribe: dict[str, Any] = {}

    class _FakeWhisperModel:
        def __init__(self, model: str, *, device: str, compute_type: str) -> None:
            captured_init["model"] = model
            captured_init["device"] = device
            captured_init["compute_type"] = compute_type

        def transcribe(self, path: str, **kwargs: Any) -> tuple[list[_Segment], _Info]:
            captured_transcribe["path"] = path
            captured_transcribe["kwargs"] = kwargs
            return [
                _Segment(start=0.0, end=0.5, text="Hello"),
                _Segment(start=0.5, end=1.2, text="world"),
            ], _Info(language="en", language_probability=0.98, duration=1.2)

    monkeypatch.setattr(AudioTranscriptionTool, "_load_whisper_model_class", staticmethod(lambda: _FakeWhisperModel))
    tool = AudioTranscriptionTool(
        config=AudioTranscriptionToolConfig(
            enabled=True,
            model="small",
            device="cpu",
            compute_type="int8",
            beam_size=3,
            vad_filter=True,
        ),
        storage=storage,
    )
    binding = tool.bindings()[0]

    result = await binding.handler(
        {"path": "uploads/hello.wav", "language": "en", "task": "transcribe"},
        ToolContext(owner_id="1"),
    )

    assert result["ok"] is True
    assert result["text"] == "Hello world"
    assert result["language"] == "en"
    assert result["language_probability"] == 0.98
    assert result["duration_seconds"] == 1.2
    assert result["segment_count"] == 2
    assert result["segments"] == [
        {"start": 0.0, "end": 0.5, "text": "Hello"},
        {"start": 0.5, "end": 1.2, "text": "world"},
    ]
    assert result["model"] == "small"
    assert result["device"] == "cpu"
    assert result["compute_type"] == "int8"
    assert captured_init == {"model": "small", "device": "cpu", "compute_type": "int8"}
    assert captured_transcribe["path"] == str(audio_file)
    assert captured_transcribe["kwargs"] == {
        "beam_size": 3,
        "vad_filter": True,
        "language": "en",
        "task": "transcribe",
    }


@pytest.mark.asyncio
async def test_audio_transcription_tool_rejects_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)

    class _FakeWhisperModel:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    monkeypatch.setattr(AudioTranscriptionTool, "_load_whisper_model_class", staticmethod(lambda: _FakeWhisperModel))
    tool = AudioTranscriptionTool(config=AudioTranscriptionToolConfig(enabled=True), storage=storage)
    binding = tool.bindings()[0]

    with pytest.raises(ValueError, match="file does not exist"):
        await binding.handler({"path": "uploads/missing.wav"}, ToolContext(owner_id="1"))


@pytest.mark.asyncio
async def test_audio_transcription_tool_returns_error_payload_on_runtime_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    audio_file = tmp_path / "uploads" / "broken.wav"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"fake-audio")

    class _FakeWhisperModel:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def transcribe(self, *_args: Any, **_kwargs: Any) -> tuple[list[_Segment], _Info]:
            raise RuntimeError("decoder failure")

    monkeypatch.setattr(AudioTranscriptionTool, "_load_whisper_model_class", staticmethod(lambda: _FakeWhisperModel))
    tool = AudioTranscriptionTool(config=AudioTranscriptionToolConfig(enabled=True), storage=storage)
    binding = tool.bindings()[0]

    result = await binding.handler({"path": "uploads/broken.wav"}, ToolContext(owner_id="1"))

    assert result["ok"] is False
    assert result["path"] == "uploads/broken.wav"
    assert "decoder failure" in result["error"]
