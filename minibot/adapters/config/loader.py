from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from minibot.config.paths import resolve_config_path
from minibot.config.schema import Settings


def load_settings(path: Path | None = None, secrets: Mapping[str, str] | None = None) -> Settings:
    resolved = resolve_config_path(path)
    if resolved.is_file():
        return Settings.from_file(resolved, secrets)
    if resolved.exists():
        raise ValueError(f"config path must be a file: {resolved}")
    return Settings()
