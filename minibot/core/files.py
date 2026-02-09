from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class StoredFileRecord(BaseModel):
    id: str
    relative_path: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    created_at: datetime
    source: str = "manual"
    owner_id: str | None = None
    channel: str | None = None
    chat_id: int | None = None
    user_id: int | None = None


class FileReadResponse(BaseModel):
    path: str
    mode: Literal["lines", "bytes"]
    offset: int
    limit: int
    content: str
    bytes_read: int
    has_more: bool


class FileStorage(Protocol):
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
    ) -> StoredFileRecord: ...

    async def list_files(
        self,
        *,
        prefix: str | None,
        limit: int,
        offset: int,
    ) -> list[StoredFileRecord]: ...

    async def read_file(
        self,
        *,
        path: str,
        mode: Literal["lines", "bytes"],
        offset: int,
        limit: int,
    ) -> FileReadResponse: ...

    def resolve_absolute_path(self, path: str) -> Path: ...

    def describe_file(
        self,
        *,
        path: str,
        owner_id: str | None,
        channel: str | None,
        chat_id: int | None,
        user_id: int | None,
        source: str = "manual",
    ) -> StoredFileRecord: ...
