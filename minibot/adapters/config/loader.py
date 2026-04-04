from __future__ import annotations

import logging
from pathlib import Path
import os

from minibot.adapters.config.schema import Settings

_logger = logging.getLogger("minibot.config")

DEFAULT_CONFIG_PATHS = (Path("config.toml"), Path("config.lua"))


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
        try:
            return Settings.from_file(candidate)
        except RuntimeError:
            if candidate.suffix.lower() == ".lua":
                _logger.warning(
                    "skipping %s: lupa not installed — run `pip install minibot[lua]` to enable Lua config support",
                    candidate,
                )
                continue
            raise
    return Settings()
