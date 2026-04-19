from __future__ import annotations

import os
from pathlib import Path

from minibot.adapters.config.schema import Settings

DEFAULT_CONFIG_PATHS = (Path("config.toml"),)


def load_settings(path: Path | None = None) -> Settings:
    env_path = os.environ.get("MINIBOT_CONFIG")
    resolved = path or (Path(env_path) if env_path else None)
    if resolved is not None:
        if resolved.is_file():
            return Settings.from_file(resolved)
        if resolved.exists():
            raise ValueError(f"config path must be a file: {resolved}")
        return Settings()

    for candidate in DEFAULT_CONFIG_PATHS:
        if not candidate.is_file():
            continue
        return Settings.from_file(candidate)
    return Settings()
