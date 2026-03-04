from __future__ import annotations

import asyncio
import threading
from typing import Any

from minibot.adapters.config.schema import AudioTranscriptionToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage


class AudioTranscriptionFacade:
    def __init__(
        self,
        *,
        config: AudioTranscriptionToolConfig,
        storage: LocalFileStorage,
        whisper_model_class: Any,
    ) -> None:
        self._config = config
        self._storage = storage
        self._whisper_model_class = whisper_model_class
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
        model = self._get_model()
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

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                self._model = self._whisper_model_class(
                    self._config.model,
                    device=self._config.device,
                    compute_type=self._config.compute_type,
                )
        return self._model

    @staticmethod
    def _transcribe_sync(model: Any, path: str, options: dict[str, Any]) -> tuple[list[Any], Any]:
        segments_iter, info = model.transcribe(path, **options)
        return list(segments_iter), info
