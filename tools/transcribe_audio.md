# transcribe_audio

## Purpose

Transcribes or translates managed audio files using `faster-whisper`.

## Availability

Enabled by `[tools.audio_transcription].enabled = true`. It requires `[tools.file_storage].enabled = true`, the `stt` extra, and ffmpeg at runtime.

## Configuration

Relevant config: `[tools.audio_transcription]`.

Important fields include `model`, `device`, `compute_type`, `beam_size`, `vad_filter`, `auto_transcribe_short_incoming`, and `auto_transcribe_max_duration_seconds`.

## Interface

Inputs:

- `path`: managed audio file path relative to `tools.file_storage.root_dir`.
- `language`: optional ISO 639-1 language hint, for example `en` or `es`.
- `task`: optional `transcribe` or `translate`; `translate` targets English.

The result is produced by the audio transcription facade and includes the transcription outcome for the managed file.

## Safety Notes

Audio decoding and model inference run on the MiniBot host. Choose device and model settings appropriate for host resources.
