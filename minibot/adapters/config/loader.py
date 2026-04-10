from __future__ import annotations

import logging
from pathlib import Path
import os

from minibot.adapters.config.schema import Settings

_logger = logging.getLogger("minibot.config")

DEFAULT_CONFIG_PATHS = (Path("config.toml"),)


def load_settings(path: Path | None = None) -> Settings:
    env_path = os.environ.get("MINIBOT_CONFIG")
    resolved = path or (Path(env_path) if env_path else None)
    if resolved is not None:
        if resolved.suffix.lower() == ".lua":
            raise ValueError(
                f"Lua config is no longer supported: {resolved}. Migrate to config.toml (see config.example.toml)."
            )
        if resolved.is_file():
            return Settings.from_file(resolved)
        if resolved.exists():
            raise ValueError(f"config path must be a file: {resolved}")
        return Settings()

    if Path("config.lua").is_file():
        _logger.warning(
            "config.lua found but Lua config support has been removed; "
            "migrate to config.toml and remove config.lua to suppress this warning"
        )

    for candidate in DEFAULT_CONFIG_PATHS:
        if not candidate.is_file():
            continue
        return Settings.from_file(candidate)
    return Settings()
