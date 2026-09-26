from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable
from typing import Any

import aiosonic
from aiosonic.multipart import MultipartForm
from aiosonic.timeout import Timeouts

from minibot.adapters.config.schema import AudioTranscriptionToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage

_REMOTE_TIMEOUT_SECONDS = 600


class AudioTranscriptionFacade:
    def __init__(
        self,
        *,
        config: AudioTranscriptionToolConfig,
        storage: LocalFileStorage,
        whisper_model_class_loader: Callable[[], Any],
    ) -> None:
        self._config = config
        self._storage = storage
        self._whisper_model_class_loader = whisper_model_class_loader
        self._model: Any | None = None
        self._model_lock = threading.Lock()

    async def transcribe_path(
        self,
        *,
        path: str,
        language: str | None,
        task: str | None,
    ) -> dict[str, Any]:
        resolved_path = self._storage.resolve_existing_file(path)
        if self._config.server_url:
            return await self._transcribe_remote(
                path=path, resolved_path=str(resolved_path), language=language, task=task
            )
        model = await asyncio.to_thread(self._get_model)
        options: dict[str, Any] = {
            "beam_size": self._config.beam_size,
            "vad_filter": self._config.vad_filter,
        }
        if language:
            options["language"] = language
        if task:
            options["task"] = task

        try:
            segments, info = await asyncio.to_thread(self._transcribe_sync, model, str(resolved_path), options)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "path": path,
                "error": str(exc),
            }

        text_parts = [str(getattr(segment, "text", "")).strip() for segment in segments]
        text = " ".join(part for part in text_parts if part).strip()
        normalized_segments = [
            {
                "start": float(getattr(segment, "start", 0.0)),
                "end": float(getattr(segment, "end", 0.0)),
                "text": str(getattr(segment, "text", "")).strip(),
            }
            for segment in segments
        ]
        return {
            "ok": True,
            "path": path,
            "text": text,
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "duration_seconds": getattr(info, "duration", None),
            "segments": normalized_segments,
            "segment_count": len(normalized_segments),
            "model": self._config.model,
            "device": self._config.device,
            "compute_type": self._config.compute_type,
        }

    async def _transcribe_remote(
        self,
        *,
        path: str,
        resolved_path: str,
        language: str | None,
        task: str | None,
    ) -> dict[str, Any]:
        server_url = str(self._config.server_url)
        form = MultipartForm()
        form.add_field("response_format", "verbose_json")
        form.add_field("beam_size", str(self._config.beam_size))
        if language:
            form.add_field("language", language)
        if task == "translate":
            form.add_field("translate", "true")
        try:
            with open(resolved_path, "rb") as audio_file:
                form.add_field("file", audio_file, os.path.basename(resolved_path))
                async with aiosonic.HTTPClient() as client:
                    # whisper.cpp sends nothing until inference finishes, so sock_read must cover the whole run.
                    timeouts = Timeouts(sock_read=_REMOTE_TIMEOUT_SECONDS, request_timeout=_REMOTE_TIMEOUT_SECONDS)
                    response = await client.post(server_url, data=form, timeouts=timeouts)
                    try:
                        body = await response.json()
                    except ValueError:
                        body = None
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "path": path, "error": str(exc) or type(exc).__name__}
        if not isinstance(body, dict):
            return {"ok": False, "path": path, "error": f"HTTP {response.status_code}: unexpected response body"}
        if response.status_code != 200 or "error" in body:
            return {"ok": False, "path": path, "error": str(body.get("error", f"HTTP {response.status_code}"))}

        segments = [
            {
                "start": float(segment.get("start", 0.0)),
                "end": float(segment.get("end", 0.0)),
                "text": str(segment.get("text", "")).strip(),
            }
            for segment in body.get("segments", [])
        ]
        return {
            "ok": True,
            "path": path,
            "text": " ".join(str(body.get("text", "")).split()),
            "language": body.get("language"),
            "language_probability": body.get("detected_language_probability"),
            "duration_seconds": body.get("duration"),
            "segments": segments,
            "segment_count": len(segments),
        }

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                whisper_model_class = self._whisper_model_class_loader()
                self._model = whisper_model_class(
                    self._config.model,
                    device=self._config.device,
                    compute_type=self._config.compute_type,
                )
        return self._model

    @staticmethod
    def _transcribe_sync(model: Any, path: str, options: dict[str, Any]) -> tuple[list[Any], Any]:
        segments_iter, info = model.transcribe(path, **options)
        return list(segments_iter), info
