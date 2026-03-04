from __future__ import annotations

from pathlib import Path

from minibot.adapters.messaging.telegram.incoming_media_mapper import TelegramIncomingMediaMapper


def test_mapper_builds_audio_targets_and_incoming_ref(tmp_path: Path) -> None:
    temp_dir = tmp_path / "uploads" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    upload_counter = {"n": 0}

    def _upload_filename(*, prefix: str, message_id: int, chat_id: int, suffix: str) -> str:
        upload_counter["n"] += 1
        return f"{prefix}_{chat_id}_{message_id}_{upload_counter['n']}{suffix}"

    mapper = TelegramIncomingMediaMapper(
        temp_dir=temp_dir,
        chat_id=10,
        message_id=20,
        caption="cap",
        relative_to_root=lambda p: str(p.relative_to(tmp_path)).replace("\\", "/"),
        upload_filename=_upload_filename,
    )

    target = mapper.audio_target(file_name="voice.mp3", file_unique_id="abc", mime_type="audio/mpeg")
    target.write_bytes(b"x")
    collision_target = mapper.audio_target(file_name="voice.mp3", file_unique_id="abc", mime_type="audio/mpeg")

    assert target.name == "voice.mp3"
    assert collision_target.name.startswith("audio_10_20_")

    ref = mapper.to_incoming_file(
        saved=target,
        mime="audio/mpeg",
        size_bytes=1,
        source="audio",
        duration_seconds=3,
    )
    assert ref.path.endswith("uploads/temp/voice.mp3")
    assert ref.caption == "cap"
    assert ref.duration_seconds == 3
