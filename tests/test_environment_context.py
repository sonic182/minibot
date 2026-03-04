from __future__ import annotations

from minibot.adapters.config.schema import Settings
from minibot.app.environment_context import build_environment_prompt_fragment


def test_environment_context_includes_cwd_and_confined_file_storage() -> None:
    settings = Settings()
    settings.tools.file_storage.enabled = True
    settings.tools.file_storage.root_dir = "./data/files"
    settings.tools.file_storage.allow_outside_root = False

    text = build_environment_prompt_fragment(settings)

    assert "Process working directory (cwd):" in text
    assert "Filesystem managed root (configured): ./data/files" in text
    assert "Filesystem mode: confined" in text


def test_environment_context_includes_yolo_mode_rule() -> None:
    settings = Settings()
    settings.tools.file_storage.enabled = True
    settings.tools.file_storage.root_dir = "./data/files"
    settings.tools.file_storage.allow_outside_root = True

    text = build_environment_prompt_fragment(settings)

    assert "Filesystem mode: yolo" in text
    assert "outside root (yolo mode)" in text
