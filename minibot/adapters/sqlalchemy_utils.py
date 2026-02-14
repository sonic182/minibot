from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url


def resolve_sqlite_storage_path(sqlite_url: str) -> Path | None:
    url = make_url(sqlite_url)
    if url.database and url.drivername.startswith("sqlite") and url.database != ":memory":
        return Path(url.database)
    return None


def ensure_parent_dir(path: Path) -> None:
    directory = path.parent
    if directory and not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
