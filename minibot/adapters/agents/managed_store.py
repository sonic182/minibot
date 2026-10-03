from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from minibot.app.agent_definitions_loader import AGENT_NAME_RE

MAX_MANAGED_DEFINITION_BYTES = 64_000


class LocalManagedAgentStore:
    """Managed agent definitions as ``<name>.md`` files inside one directory.

    The directory is the whole confinement: a name that does not match the agent-name pattern is
    refused before it becomes a path, so ``..`` and separators cannot escape, and a resolved path
    whose parent is not the managed directory is refused as a second check. A write goes to a
    temporary file in that directory and is moved into place with ``os.replace``, so a crash cannot
    leave a half-written definition for the loader to read.
    """

    def __init__(self, directory: str | Path, *, max_write_bytes: int = MAX_MANAGED_DEFINITION_BYTES) -> None:
        self._directory = Path(directory)
        self._max_write_bytes = max_write_bytes

    @property
    def directory(self) -> Path:
        return self._directory

    def list_names(self) -> list[str]:
        if not self._directory.is_dir():
            return []
        return sorted(path.stem for path in self._directory.glob("*.md"))

    def exists(self, name: str) -> bool:
        return self._path(name).is_file()

    def write(self, name: str, content: str) -> None:
        payload = content.encode("utf-8")
        if len(payload) > self._max_write_bytes:
            raise ValueError(f"agent definition is {len(payload)} bytes, over the {self._max_write_bytes}-byte limit")
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise

    def delete(self, name: str) -> None:
        self._path(name).unlink()

    def _path(self, name: str) -> Path:
        if not AGENT_NAME_RE.fullmatch(name):
            raise ValueError(f"agent name '{name}' must match {AGENT_NAME_RE.pattern}")
        root = self._directory.resolve()
        path = root / f"{name}.md"
        if path.is_symlink():
            raise ValueError(f"refusing to write through the symlink at {path}")
        if path.resolve().parent != root:
            raise ValueError(f"agent name '{name}' resolves outside {root}")
        return path
