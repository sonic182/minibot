from __future__ import annotations

from pathlib import Path
import os

from minibot.adapters.config.schema import Settings

DEFAULT_CONFIG_PATH = Path("config.toml")


def resolve_settings_path(path: Path | None = None) -> Path:
    return (path or Path(os.environ.get("MINIBOT_CONFIG", DEFAULT_CONFIG_PATH))).expanduser()


def load_settings(path: Path | None = None) -> Settings:
    resolved = resolve_settings_path(path)
    if resolved.exists():
        return Settings.from_file(resolved)
    return Settings()
