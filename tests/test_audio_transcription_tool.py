from __future__ import annotations

import asyncio
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
    loader_calls: list[None] = []

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

    def _load_whisper_model_class() -> type[_FakeWhisperModel]:
        loader_calls.append(None)
        return _FakeWhisperModel

    monkeypatch.setattr(
        AudioTranscriptionTool,
        "_load_whisper_model_class",
        staticmethod(_load_whisper_model_class),
    )
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
    assert loader_calls == []

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
    assert loader_calls == [None]
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


@pytest.mark.asyncio
async def test_audio_transcription_tool_offloads_transcription_with_to_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    audio_file = tmp_path / "uploads" / "threaded.wav"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"fake-audio")
    to_thread_calls: list[str] = []

    class _FakeWhisperModel:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def transcribe(self, *_args: Any, **_kwargs: Any) -> tuple[list[_Segment], _Info]:
            return [_Segment(start=0.0, end=0.2, text="ok")], _Info(
                language="en",
                language_probability=1.0,
                duration=0.2,
            )

    async def _fake_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(getattr(func, "__name__", "unknown"))
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(AudioTranscriptionTool, "_load_whisper_model_class", staticmethod(lambda: _FakeWhisperModel))
    tool = AudioTranscriptionTool(config=AudioTranscriptionToolConfig(enabled=True), storage=storage)
    binding = tool.bindings()[0]

    result = await binding.handler({"path": "uploads/threaded.wav"}, ToolContext(owner_id="1"))

    assert result["ok"] is True
    assert result["text"] == "ok"
    assert "_get_model" in to_thread_calls
    assert "_transcribe_sync" in to_thread_calls


@pytest.mark.asyncio
async def test_audio_transcription_tool_uses_whisper_server_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    audio_file = tmp_path / "uploads" / "voice.ogg"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"fake-audio")
    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200

        async def json(self) -> dict[str, Any]:
            return {
                "text": " Hola\n mundo\n",
                "language": "spanish",
                "detected_language_probability": 0.9,
                "duration": 1.5,
                "segments": [{"start": 0.0, "end": 1.5, "text": " Hola mundo"}],
            }

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, url: str, *, data: Any, **_kwargs: Any) -> _FakeResponse:
            captured["url"] = url
            captured["body"] = b"".join([chunk async for chunk in data.get_buffer()])
            captured["boundary"] = data.boundary
            return _FakeResponse()

    monkeypatch.setattr("minibot.llm.tools.audio_transcription_facade.aiosonic.HTTPClient", _FakeClient)
    monkeypatch.setattr(AudioTranscriptionTool, "_load_whisper_model_class", staticmethod(lambda: 1 / 0))
    tool = AudioTranscriptionTool(
        config=AudioTranscriptionToolConfig(enabled=True, server_url="http://whisper:8080/inference"),
        storage=storage,
    )

    result = await tool.bindings()[0].handler(
        {"path": "uploads/voice.ogg", "language": None, "task": "translate"},
        ToolContext(owner_id="1"),
    )

    assert result["ok"] is True
    assert result["text"] == "Hola mundo"
    assert result["language"] == "spanish"
    assert result["duration_seconds"] == 1.5
    assert result["segments"] == [{"start": 0.0, "end": 1.5, "text": "Hola mundo"}]
    assert captured["url"] == "http://whisper:8080/inference"
    body = captured["body"]
    assert b"fake-audio\r\n--" + captured["boundary"].encode() in body
    assert b'name="language"' not in body
    assert b'name="translate"\r\n\r\ntrue\r\n' in body
    assert b'name="response_format"\r\n\r\nverbose_json\r\n' in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body", "expected_error"),
    [
        (502, ValueError("Expecting value"), "HTTP 502: unexpected response body"),
        (200, ["not", "a", "dict"], "HTTP 200: unexpected response body"),
        (400, {"error": "bad audio"}, "bad audio"),
    ],
)
async def test_audio_transcription_tool_reports_whisper_server_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status_code: int, body: Any, expected_error: str
) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "voice.ogg").write_bytes(b"fake-audio")

    class _FakeResponse:
        async def json(self) -> Any:
            if isinstance(body, Exception):
                raise body
            return body

    _FakeResponse.status_code = status_code

    class _FakeClient:
        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, *_args: Any, **_kwargs: Any) -> _FakeResponse:
            return _FakeResponse()

    monkeypatch.setattr("minibot.llm.tools.audio_transcription_facade.aiosonic.HTTPClient", _FakeClient)
    tool = AudioTranscriptionTool(
        config=AudioTranscriptionToolConfig(enabled=True, server_url="http://whisper:8080/inference"),
        storage=storage,
    )

    result = await tool.bindings()[0].handler(
        {"path": "uploads/voice.ogg", "language": None, "task": None}, ToolContext(owner_id="1")
    )

    assert result == {"ok": False, "path": "uploads/voice.ogg", "error": expected_error}


def test_audio_transcription_config_rejects_non_http_server_url() -> None:
    with pytest.raises(ValueError):
        AudioTranscriptionToolConfig(server_url="ftp://whisper/inference")
