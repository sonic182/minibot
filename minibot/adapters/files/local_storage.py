from __future__ import annotations

from datetime import datetime, timezone
import mimetypes
from pathlib import Path
import shutil
from typing import Literal


class LocalFileStorage:
    def __init__(self, root_dir: str, max_write_bytes: int) -> None:
        self._root = Path(root_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_write_bytes = max_write_bytes

    @property
    def root_dir(self) -> Path:
        return self._root

    def list_files(self, folder: str | None = None) -> list[dict[str, str | int | bool]]:
        target = self.resolve_dir(folder)
        entries: list[dict[str, str | int | bool]] = []
        for item in sorted(target.iterdir(), key=lambda path: path.name.lower()):
            stat = item.stat()
            entries.append(
                {
                    "name": item.name,
                    "path": self._relative_to_root(item),
                    "is_dir": item.is_dir(),
                    "size_bytes": int(stat.st_size),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        return entries

    def glob_files(
        self,
        pattern: str,
        folder: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, str | int | bool]]:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("pattern must be a non-empty string")
        if limit is not None and limit < 1:
            raise ValueError("limit must be >= 1")

        target = self.resolve_dir(folder)
        matches: list[Path] = []
        try:
            for candidate in target.rglob(pattern.strip()):
                if not candidate.is_file():
                    continue
                matches.append(candidate)
                if limit is not None and len(matches) >= limit:
                    break
        except ValueError as exc:
            raise ValueError("invalid glob pattern") from exc

        entries: list[dict[str, str | int | bool]] = []
        for item in sorted(matches, key=lambda path: self._relative_to_root(path).lower()):
            stat = item.stat()
            entries.append(
                {
                    "name": item.name,
                    "path": self._relative_to_root(item),
                    "is_dir": False,
                    "size_bytes": int(stat.st_size),
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                }
            )
        return entries

    def create_text_file(self, path: str, content: str, overwrite: bool = False) -> dict[str, str | int]:
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self._max_write_bytes:
            raise ValueError(f"content exceeds max_write_bytes ({self._max_write_bytes})")

        target = self.resolve_file(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            raise ValueError("file already exists; set overwrite=true to replace it")

        target.write_text(content, encoding="utf-8")
        return {
            "path": self._relative_to_root(target),
            "bytes_written": len(content_bytes),
        }

    def ensure_upload_dir(self, uploads_subdir: str) -> Path:
        upload_dir = self.resolve_dir(uploads_subdir, create=True)
        return upload_dir

    def move_file(self, source_path: str, destination_path: str, overwrite: bool = False) -> dict[str, str | bool]:
        source = self.resolve_existing_file(source_path)
        destination = self.resolve_file(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_dir():
                raise ValueError("destination path is a directory")
            if not overwrite:
                raise ValueError("destination already exists; set overwrite=true to replace it")
        source.replace(destination)
        return {
            "source_path": self._relative_to_root(source),
            "destination_path": self._relative_to_root(destination),
            "overwrite": overwrite,
        }

    def delete_file(
        self,
        path: str,
        *,
        recursive: bool = False,
        target: Literal["any", "file", "folder"] = "any",
    ) -> dict[str, str | bool | int]:
        candidate = self.resolve_file(path)
        relative_path = self._relative_to_root(candidate)
        if not candidate.exists():
            return {
                "path": relative_path,
                "deleted": False,
                "deleted_count": 0,
                "target_type": target,
            }

        if candidate.is_file():
            if target == "folder":
                return {
                    "path": relative_path,
                    "deleted": False,
                    "deleted_count": 0,
                    "target_type": "file",
                }
            candidate.unlink()
            return {
                "path": relative_path,
                "deleted": True,
                "deleted_count": 1,
                "target_type": "file",
            }

        if not candidate.is_dir():
            raise ValueError("path is not a file or folder")
        if target == "file":
            return {
                "path": relative_path,
                "deleted": False,
                "deleted_count": 0,
                "target_type": "folder",
            }

        if recursive:
            file_count = sum(1 for item in candidate.rglob("*") if item.is_file())
            dir_count = sum(1 for item in candidate.rglob("*") if item.is_dir())
            shutil.rmtree(candidate)
            return {
                "path": relative_path,
                "deleted": True,
                "deleted_count": file_count + dir_count + 1,
                "target_type": "folder",
            }

        try:
            candidate.rmdir()
        except OSError as exc:
            raise ValueError("folder is not empty; set recursive=true to delete recursively") from exc
        return {
            "path": relative_path,
            "deleted": True,
            "deleted_count": 1,
            "target_type": "folder",
        }

    def file_info(self, path: str) -> dict[str, str | int | bool]:
        target = self.resolve_existing_file(path)
        stat = target.stat()
        extension = target.suffix.lower()
        mime_type, _ = mimetypes.guess_type(str(target), strict=False)
        return {
            "path": self._relative_to_root(target),
            "name": target.name,
            "extension": extension,
            "mime": mime_type or "application/octet-stream",
            "size_bytes": int(stat.st_size),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "is_image": (mime_type or "").startswith("image/"),
        }

    def resolve_existing_file(self, path: str) -> Path:
        candidate = self.resolve_file(path)
        if not candidate.exists():
            raise ValueError("file does not exist")
        if not candidate.is_file():
            raise ValueError("path is not a file")
        return candidate

    def resolve_dir(self, folder: str | None, create: bool = False) -> Path:
        relative_folder = (folder or ".").strip()
        if not relative_folder:
            relative_folder = "."
        target = self._resolve_within_root(relative_folder)
        if create:
            target.mkdir(parents=True, exist_ok=True)
        if not target.exists() or not target.is_dir():
            raise ValueError("folder does not exist")
        return target

    def resolve_file(self, path: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")
        return self._resolve_within_root(path)

    def _resolve_within_root(self, relative_path: str) -> Path:
        candidate_path = Path(relative_path)
        if candidate_path.is_absolute():
            raise ValueError("path must be relative to managed root")
        resolved = (self._root / candidate_path).resolve()
        if not resolved.is_relative_to(self._root):
            raise ValueError("path escapes managed root")
        return resolved

    def _relative_to_root(self, path: Path) -> str:
        return str(path.relative_to(self._root)).replace("\\", "/")
