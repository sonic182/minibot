from __future__ import annotations

from typing import Any

from llm_async.models import Tool

from minibot.adapters.config.schema import AudioTranscriptionToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.llm.tools.arg_utils import optional_str, require_non_empty_str
from minibot.llm.tools.audio_transcription_facade import AudioTranscriptionFacade
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_string, strict_object


class AudioTranscriptionTool:
    def __init__(
        self,
        config: AudioTranscriptionToolConfig,
        storage: LocalFileStorage,
        facade: AudioTranscriptionFacade | None = None,
    ) -> None:
        self._config = config
        self._facade = facade or AudioTranscriptionFacade(
            config=config,
            storage=storage,
            whisper_model_class=self._load_whisper_model_class(),
        )

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        return Tool(
            name="transcribe_audio",
            description=load_tool_description("transcribe_audio"),
            parameters=strict_object(
                properties={
                    "path": {
                        "type": "string",
                        "description": "Managed audio file path relative to tools.file_storage.root_dir.",
                    },
                    "language": nullable_string("Optional language hint (ISO 639-1, for example en, es)."),
                    "task": {
                        **nullable_string("Optional mode: transcribe (source language) or translate (to English)."),
                        "enum": ["transcribe", "translate", None],
                    },
                },
                required=["path", "language", "task"],
            ),
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        path = require_non_empty_str(payload, "path")
        language = optional_str(payload.get("language"))
        task = optional_str(payload.get("task"))
        if task is not None and task not in {"transcribe", "translate"}:
            raise ValueError("task must be one of: transcribe, translate")
        return await self._facade.transcribe_path(path=path, language=language, task=task)

    @staticmethod
    def _load_whisper_model_class() -> Any:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "faster-whisper is required for tools.audio_transcription. "
                "Install with `poetry install --extras stt` or `poetry install --all-extras`."
            ) from exc
        return WhisperModel
