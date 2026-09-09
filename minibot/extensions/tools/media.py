from __future__ import annotations

from minibot.app.extensions import ExtensionContext

from ._storage import managed_storage


def register(mb: ExtensionContext) -> None:
    if not mb.settings.tools.audio_transcription.enabled:
        return
    from minibot.llm.tools.audio_transcription import AudioTranscriptionTool

    storage = managed_storage(
        mb.settings,
        error_message="tools.audio_transcription.enabled requires tools.file_storage.enabled",
    )
    mb.add_tool(AudioTranscriptionTool(config=mb.settings.tools.audio_transcription, storage=storage).bindings())
