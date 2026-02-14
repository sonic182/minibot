from __future__ import annotations

from pathlib import Path


def normalize_path_separators(value: str) -> str:
    return value.replace("\\", "/")


def to_posix_relative(path: Path, root: Path) -> str:
    return normalize_path_separators(str(path.relative_to(root)))
