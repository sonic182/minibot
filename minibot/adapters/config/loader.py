from __future__ import annotations

import os
from pathlib import Path

from minibot.adapters.config.schema import Settings

DEFAULT_CONFIG_PATHS = (Path("config.toml"),)


def resolve_config_path(path: Path | None = None) -> Path:
    env_path = os.environ.get("MINIBOT_CONFIG")
    return path or (Path(env_path) if env_path else DEFAULT_CONFIG_PATHS[0])


def load_settings(path: Path | None = None) -> Settings:
    resolved = resolve_config_path(path)
    if resolved.is_file():
        return Settings.from_file(resolved)
    if resolved.exists():
        raise ValueError(f"config path must be a file: {resolved}")
    return Settings()
