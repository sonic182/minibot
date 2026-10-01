from __future__ import annotations

import os
from pathlib import Path

DEFAULT_CONFIG_PATHS = (Path("config.toml"),)


def resolve_config_path(path: Path | None = None) -> Path:
    env_path = os.environ.get("MINIBOT_CONFIG")
    return path or (Path(env_path) if env_path else DEFAULT_CONFIG_PATHS[0])
