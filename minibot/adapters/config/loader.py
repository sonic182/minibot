from __future__ import annotations

from pathlib import Path
import os

from minibot.adapters.config.schema import Settings

DEFAULT_CONFIG_PATHS = (Path("config.toml"), Path("config.lua"))


def load_settings(path: Path | None = None) -> Settings:
    env_path = os.environ.get("MINIBOT_CONFIG")
    resolved = path or (Path(env_path) if env_path else None)
    if resolved is not None:
        if resolved.exists():
            return Settings.from_file(resolved)
        return Settings()

    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return Settings.from_file(candidate)
    return Settings()
