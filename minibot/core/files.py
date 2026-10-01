from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol


class FileStorage(Protocol):
    @property
    def root_dir(self) -> Path: ...

    @property
    def allow_outside_root(self) -> bool: ...

    def list_files(self, folder: str | None = None) -> list[dict[str, str | int | bool]]: ...

    def glob_files(
        self,
        *,
        pattern: str,
        folder: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, str | int | bool]]: ...

    def create_text_file(self, path: str, content: str, overwrite: bool = False) -> dict[str, str | int]: ...

    def create_managed_temp_text_file(
        self,
        *,
        subdir: str,
        stem: str,
        content: str,
        suffix: str = ".txt",
    ) -> dict[str, str | int]: ...

    def create_managed_temp_bytes_file(
        self,
        *,
        subdir: str,
        stem: str,
        content: bytes,
        suffix: str = ".txt",
    ) -> dict[str, str | int]: ...

    def ensure_upload_dir(self, uploads_subdir: str) -> Path: ...

    def move_file(self, source_path: str, destination_path: str, overwrite: bool = False) -> dict[str, str | bool]: ...

    def delete_file(
        self,
        path: str,
        *,
        recursive: bool = False,
        target: Literal["any", "file", "folder"] = "any",
    ) -> dict[str, str | int | bool]: ...

    def read_text_file(self, path: str, max_bytes: int = 131_072) -> dict[str, str | int | bool]: ...

    def read_text_lines(
        self,
        path: str,
        *,
        offset: int,
        limit: int,
    ) -> dict[str, str | int | bool | None]: ...

    def file_info(self, path: str) -> dict[str, str | int | bool]: ...

    def resolve_existing_file(self, path: str) -> Path: ...

    def resolve_dir(self, folder: str | None, create: bool = False) -> Path: ...

    def resolve_file(self, path: str) -> Path: ...

    def display_path(self, path: Path) -> str: ...
