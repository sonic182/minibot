from __future__ import annotations

from minibot.adapters.config.schema import Settings
from minibot.adapters.files.local_storage import LocalFileStorage


def managed_storage(settings: Settings, *, error_message: str | None = None) -> LocalFileStorage | None:
    config = settings.tools.file_storage
    if not config.enabled:
        if error_message:
            raise ValueError(error_message)
        return None
    return LocalFileStorage(
        root_dir=config.root_dir,
        max_write_bytes=config.max_write_bytes,
        allow_outside_root=config.allow_outside_root,
    )
