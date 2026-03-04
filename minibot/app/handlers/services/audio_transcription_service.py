from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Sequence

from minibot.app.incoming_files_context import incoming_files_from_metadata
from minibot.core.channels import ChannelMessage, IncomingFileRef
from minibot.llm.tools.base import ToolContext

from minibot.app.handlers.services.tool_audio_executor import AudioTranscriptionExecutor


@dataclass(frozen=True)
class AudioAutoTranscribePolicy:
    enabled: bool = True
    max_duration_seconds: int = 45


@dataclass(frozen=True)
class TranscribedAudio:
    item: IncomingFileRef
    text: str


@dataclass(frozen=True)
class AudioTranscriptionError:
    item: IncomingFileRef
    error: str


@dataclass(frozen=True)
class AudioAutoTranscriptionResult:
    attempted_count: int
    successes: list[TranscribedAudio]
    errors: list[AudioTranscriptionError]

    @property
    def has_updates(self) -> bool:
        return bool(self.successes or self.errors)


class AudioAutoTranscriptionService:
    def __init__(
        self,
        *,
        executor: AudioTranscriptionExecutor,
        policy: AudioAutoTranscribePolicy,
        logger: logging.Logger | None = None,
    ) -> None:
        self._executor = executor
        self._policy = policy
        self._logger = logger or logging.getLogger("minibot.audio_auto_transcription")

    def select_candidates(self, incoming_files: Sequence[IncomingFileRef]) -> list[IncomingFileRef]:
        if not self._policy.enabled:
            return []
        max_duration = max(self._policy.max_duration_seconds, 1)
        candidates: list[IncomingFileRef] = []
        for item in incoming_files:
            duration = item.duration_seconds
            if duration is None or duration > max_duration:
                continue
            source = item.source.strip().lower()
            mime = item.mime.strip().lower()
            if source in {"audio", "voice"} or mime.startswith("audio/"):
                candidates.append(item)
        return candidates

    async def transcribe_candidates(
        self,
        *,
        candidates: Sequence[IncomingFileRef],
        context: ToolContext,
    ) -> AudioAutoTranscriptionResult:
        successes: list[TranscribedAudio] = []
        errors: list[AudioTranscriptionError] = []
        for item in candidates:
            try:
                result = await self._executor.transcribe(path=item.path, context=context, task="transcribe")
            except Exception as exc:  # noqa: BLE001
                errors.append(AudioTranscriptionError(item=item, error=str(exc)))
                continue
            if not isinstance(result, dict):
                errors.append(AudioTranscriptionError(item=item, error="invalid transcription result"))
                continue
            if result.get("ok") is False:
                errors.append(
                    AudioTranscriptionError(item=item, error=str(result.get("error", "transcription failed")))
                )
                continue
            text = str(result.get("text", "")).strip()
            if not text:
                errors.append(AudioTranscriptionError(item=item, error="empty transcript"))
                continue
            successes.append(TranscribedAudio(item=item, text=text))
        self._logger.info(
            "auto-transcribe incoming audio completed",
            extra={
                "candidate_count": len(candidates),
                "transcribed_count": len(successes),
                "error_count": len(errors),
                "max_duration_seconds": self._policy.max_duration_seconds,
            },
        )
        return AudioAutoTranscriptionResult(attempted_count=len(candidates), successes=successes, errors=errors)

    def build_prompt_prefix(self, result: AudioAutoTranscriptionResult) -> str:
        if not result.has_updates:
            return ""
        lines = ["Automatic audio transcriptions from incoming files:"]
        for entry in result.successes:
            item = entry.item
            duration_label = item.duration_seconds if item.duration_seconds is not None else "unknown"
            lines.append(f"- {item.filename} (path={item.path}, duration={duration_label}s): {entry.text}")
        if result.errors:
            lines.append("Automatic transcription errors:")
            for error in result.errors:
                lines.append(f"- {error.item.filename}: {error.error}")
        if result.successes:
            lines.append("Treat these transcriptions as user instructions for this turn.")
        return "\n".join(lines)

    def apply_to_model_text(self, model_text: str, result: AudioAutoTranscriptionResult) -> str:
        prefix = self.build_prompt_prefix(result)
        if not prefix:
            return model_text
        if model_text:
            return f"{model_text}\n\n{prefix}"
        return prefix

    async def transcribe_incoming_audio(
        self,
        *,
        message: ChannelMessage,
        context: ToolContext,
    ) -> AudioAutoTranscriptionResult:
        incoming_files = incoming_files_from_metadata(message.metadata)
        candidates = self.select_candidates(incoming_files)
        if not candidates:
            return AudioAutoTranscriptionResult(attempted_count=0, successes=[], errors=[])
        return await self.transcribe_candidates(candidates=candidates, context=context)
