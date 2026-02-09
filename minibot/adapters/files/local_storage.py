from __future__ import annotations

import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import Path

from minibot.adapters.config.schema import FileStorageToolConfig
from minibot.core.files import FileReadResponse, StoredFileRecord


class LocalFileStorage:
    def __init__(self, config: FileStorageToolConfig) -> None:
        self._root = Path(config.root_dir).expanduser().resolve()
        self._max_write_bytes = int(config.max_write_bytes)
        self._max_read_bytes = int(config.max_read_bytes)
        self._max_read_lines = int(config.max_read_lines)
        self._root.mkdir(parents=True, exist_ok=True)

    async def write_text(
        self,
        *,
        path: str,
        content: str,
        owner_id: str | None,
        channel: str | None,
        chat_id: int | None,
        user_id: int | None,
        source: str,
    ) -> StoredFileRecord:
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        payload = content.encode("utf-8")
        if len(payload) > self._max_write_bytes:
            raise ValueError(f"content exceeds max_write_bytes ({len(payload)} > {self._max_write_bytes})")
        target = self.resolve_absolute_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return self.describe_file(
            path=path,
            owner_id=owner_id,
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            source=source,
        )

    async def list_files(self, *, prefix: str | None, limit: int, offset: int) -> list[StoredFileRecord]:
        safe_limit = max(1, limit)
        safe_offset = max(0, offset)
        base = self._root
        if prefix:
            base = self.resolve_absolute_path(prefix)
            if base.exists() and base.is_file():
                return [
                    self.describe_file(
                        path=str(base.relative_to(self._root)),
                        owner_id=None,
                        channel=None,
                        chat_id=None,
                        user_id=None,
                    )
                ]
            if not base.exists():
                return []
        candidates: list[Path] = []
        for entry in base.rglob("*"):
            if entry.is_file():
                candidates.append(entry)
        candidates.sort(key=lambda item: str(item.relative_to(self._root)))
        selected = candidates[safe_offset : safe_offset + safe_limit]
        return [
            self.describe_file(
                path=str(item.relative_to(self._root)),
                owner_id=None,
                channel=None,
                chat_id=None,
                user_id=None,
            )
            for item in selected
        ]

    async def read_file(self, *, path: str, mode: str, offset: int, limit: int) -> FileReadResponse:
        normalized_mode = mode.strip().lower() if isinstance(mode, str) else "lines"
        if normalized_mode not in {"lines", "bytes"}:
            raise ValueError("mode must be 'lines' or 'bytes'")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if limit <= 0:
            raise ValueError("limit must be >= 1")
        target = self.resolve_absolute_path(path)
        relative = str(target.relative_to(self._root))
        if normalized_mode == "lines":
            return self._read_lines(
                path=relative,
                target=target,
                offset=offset,
                limit=min(limit, self._max_read_lines),
            )
        return self._read_bytes(path=relative, target=target, offset=offset, limit=min(limit, self._max_read_bytes))

    def resolve_absolute_path(self, path: str) -> Path:
        if not isinstance(path, str):
            raise ValueError("path must be a string")
        normalized = path.strip()
        if not normalized:
            raise ValueError("path cannot be empty")
        target = (self._root / normalized).resolve()
        if not self._is_within_root(target):
            raise ValueError("path must stay inside file storage root")
        return target

    def describe_file(
        self,
        *,
        path: str,
        owner_id: str | None,
        channel: str | None,
        chat_id: int | None,
        user_id: int | None,
        source: str = "manual",
    ) -> StoredFileRecord:
        absolute_path = self.resolve_absolute_path(path)
        if not absolute_path.exists() or not absolute_path.is_file():
            raise ValueError("file not found")
        stat = absolute_path.stat()
        relative_path = str(absolute_path.relative_to(self._root))
        mime_type, _ = mimetypes.guess_type(relative_path)
        return StoredFileRecord(
            id=self._build_file_id(relative_path),
            relative_path=relative_path,
            mime_type=mime_type or "application/octet-stream",
            size_bytes=int(stat.st_size),
            created_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            source=source,
            owner_id=owner_id,
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
        )

    def _is_within_root(self, path: Path) -> bool:
        try:
            path.relative_to(self._root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _build_file_id(relative_path: str) -> str:
        return hashlib.sha1(relative_path.encode("utf-8"), usedforsecurity=False).hexdigest()

    def _read_lines(self, *, path: str, target: Path, offset: int, limit: int) -> FileReadResponse:
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("file is not valid utf-8 text") from exc
        selected = lines[offset : offset + limit]
        has_more = offset + len(selected) < len(lines)
        content = "\n".join(selected)
        return FileReadResponse(
            path=path,
            mode="lines",
            offset=offset,
            limit=limit,
            content=content,
            bytes_read=len(content.encode("utf-8")),
            has_more=has_more,
        )

    def _read_bytes(self, *, path: str, target: Path, offset: int, limit: int) -> FileReadResponse:
        payload = target.read_bytes()
        chunk = payload[offset : offset + limit]
        has_more = offset + len(chunk) < len(payload)
        text = chunk.decode("utf-8", errors="replace")
        return FileReadResponse(
            path=path,
            mode="bytes",
            offset=offset,
            limit=limit,
            content=text,
            bytes_read=len(chunk),
            has_more=has_more,
        )
