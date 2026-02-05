from __future__ import annotations

from pathlib import Path
import os

from .schema import Settings

DEFAULT_CONFIG_PATH = Path("config.toml")


def load_settings(path: Path | None = None) -> Settings:
    resolved = path or Path(os.environ.get("MINIBOT_CONFIG", DEFAULT_CONFIG_PATH))
    if resolved.exists():
        return Settings.from_file(resolved)
    return Settings()
