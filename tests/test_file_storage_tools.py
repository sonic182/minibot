from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.core.agent_runtime import AppendMessageDirective, ToolResult
from minibot.core.events import OutboundFileEvent
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.file_storage import FileStorageTool


class _EventBusStub:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def test_local_storage_creates_and_lists_files(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)

    result = storage.create_text_file(path="notes/today.md", content="# hello", overwrite=False)
    entries = storage.list_files("notes")

    assert result["path"] == "notes/today.md"
    assert result["bytes_written"] == len("# hello".encode("utf-8"))
    assert len(entries) == 1
    assert entries[0]["name"] == "today.md"
    assert entries[0]["is_dir"] is False


def test_local_storage_blocks_path_escape(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)

    with pytest.raises(ValueError):
        storage.create_text_file(path="../outside.txt", content="nope", overwrite=False)


def test_local_storage_moves_and_deletes_files(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="temp/report.txt", content="ok", overwrite=False)

    moved = storage.move_file("temp/report.txt", "archive/report.txt", overwrite=False)
    deleted = storage.delete_file("archive/report.txt")

    assert moved["source_path"] == "temp/report.txt"
    assert moved["destination_path"] == "archive/report.txt"
    assert deleted["path"] == "archive/report.txt"
    assert deleted["deleted_count"] == 1
    assert not (tmp_path / "archive" / "report.txt").exists()


def test_local_storage_delete_file_returns_zero_when_missing(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)

    deleted = storage.delete_file("missing.txt")

    assert deleted["path"] == "missing.txt"
    assert deleted["deleted"] is False
    assert deleted["deleted_count"] == 0


def test_local_storage_file_info_returns_basic_metadata(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="docs/report.txt", content="hello", overwrite=False)

    info = storage.file_info("docs/report.txt")

    assert info["path"] == "docs/report.txt"
    assert info["name"] == "report.txt"
    assert info["extension"] == ".txt"
    assert info["size_bytes"] == 5
    assert info["mime"] == "text/plain"
    assert info["is_image"] is False


def test_local_storage_glob_files_matches_nested_paths(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="notes/today.md", content="# today", overwrite=False)
    storage.create_text_file(path="notes/archive/old.md", content="# old", overwrite=False)
    storage.create_text_file(path="notes/readme.txt", content="hello", overwrite=False)

    matches = storage.glob_files(pattern="**/*.md", folder="notes")

    assert [entry["path"] for entry in matches] == ["notes/archive/old.md", "notes/today.md"]


@pytest.mark.asyncio
async def test_send_file_publishes_outbound_file_event(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="docs/report.txt", content="ok", overwrite=False)
    event_bus = _EventBusStub()
    tool = FileStorageTool(storage=storage, event_bus=cast(Any, event_bus))
    send_binding = next(binding for binding in tool.bindings() if binding.tool.name == "send_file")

    result = await send_binding.handler(
        {"path": "docs/report.txt", "caption": "latest"},
        ToolContext(owner_id="1", channel="telegram", chat_id=99, user_id=1),
    )

    assert isinstance(result, dict)
    assert result["ok"] is True
    assert len(event_bus.events) == 1
    outbound = event_bus.events[0]
    assert isinstance(outbound, OutboundFileEvent)
    assert outbound.response.chat_id == 99
    assert outbound.response.caption == "latest"
    assert Path(outbound.response.file_path).name == "report.txt"


@pytest.mark.asyncio
async def test_move_and_delete_tools_manage_files(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="temp/report.txt", content="ok", overwrite=False)
    tool = FileStorageTool(storage=storage)
    move_binding = next(binding for binding in tool.bindings() if binding.tool.name == "move_file")
    delete_binding = next(binding for binding in tool.bindings() if binding.tool.name == "delete_file")

    moved = await move_binding.handler(
        {"source_path": "temp/report.txt", "destination_path": "archive/report.txt"},
        ToolContext(owner_id="1", channel="telegram", chat_id=99, user_id=1),
    )
    deleted = await delete_binding.handler(
        {"path": "archive/report.txt"},
        ToolContext(owner_id="1", channel="telegram", chat_id=99, user_id=1),
    )

    assert isinstance(moved, dict)
    assert isinstance(deleted, dict)
    assert moved["ok"] is True
    assert moved["destination_path"] == "archive/report.txt"
    assert deleted["ok"] is True
    assert deleted["deleted"] is True
    assert deleted["deleted_count"] == 1
    assert deleted["message"] == "Deleted file successfully: archive/report.txt"


@pytest.mark.asyncio
async def test_delete_file_tool_reports_not_found(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    tool = FileStorageTool(storage=storage)
    delete_binding = next(binding for binding in tool.bindings() if binding.tool.name == "delete_file")

    deleted = await delete_binding.handler(
        {"path": "missing.txt"},
        ToolContext(owner_id="1", channel="telegram", chat_id=99, user_id=1),
    )

    assert isinstance(deleted, dict)
    assert deleted["ok"] is True
    assert deleted["deleted"] is False
    assert deleted["deleted_count"] == 0
    assert deleted["message"] == "No file found to delete: missing.txt"


@pytest.mark.asyncio
async def test_file_info_tool_returns_metadata(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="docs/a.txt", content="abc", overwrite=False)
    tool = FileStorageTool(storage=storage)
    info_binding = next(binding for binding in tool.bindings() if binding.tool.name == "file_info")

    result = await info_binding.handler(
        {"path": "docs/a.txt"},
        ToolContext(owner_id="1", channel="telegram", chat_id=99, user_id=1),
    )

    assert isinstance(result, dict)
    assert result["ok"] is True
    assert result["path"] == "docs/a.txt"
    assert result["extension"] == ".txt"
    assert result["size_bytes"] == 3


@pytest.mark.asyncio
async def test_glob_files_tool_lists_matching_files(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="docs/a.md", content="A", overwrite=False)
    storage.create_text_file(path="docs/sub/b.md", content="B", overwrite=False)
    storage.create_text_file(path="docs/sub/c.txt", content="C", overwrite=False)
    tool = FileStorageTool(storage=storage)
    glob_binding = next(binding for binding in tool.bindings() if binding.tool.name == "glob_files")

    result = await glob_binding.handler(
        {"pattern": "**/*.md", "folder": "docs", "limit": 1},
        ToolContext(owner_id="1", channel="telegram", chat_id=99, user_id=1),
    )

    assert isinstance(result, dict)
    assert result["pattern"] == "**/*.md"
    assert result["limit"] == 1
    assert result["count"] == 1
    assert result["entries"][0]["path"] == "docs/a.md"


@pytest.mark.asyncio
async def test_self_insert_artifact_returns_directives(tmp_path: Path) -> None:
    root = tmp_path / "files"
    storage = LocalFileStorage(root_dir=str(root), max_write_bytes=1000)
    source = root / "uploads" / "sample.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("hello", encoding="utf-8")
    tool = FileStorageTool(storage=storage)
    binding = next(binding for binding in tool.bindings() if binding.tool.name == "self_insert_artifact")

    result = await binding.handler(
        {
            "path": "uploads/sample.txt",
            "as": "file",
            "role": "user",
            "text": "Review this file",
        },
        ToolContext(owner_id="1", channel="telegram", chat_id=99, user_id=1),
    )

    assert isinstance(result, ToolResult)
    assert result.content["status"] == "ok"
    assert result.content["path"] == "uploads/sample.txt"
    assert len(result.directives) == 1
    assert isinstance(result.directives[0], AppendMessageDirective)


@pytest.mark.asyncio
async def test_self_insert_artifact_rejects_non_image_for_image_mode(tmp_path: Path) -> None:
    root = tmp_path / "files"
    storage = LocalFileStorage(root_dir=str(root), max_write_bytes=1000)
    source = root / "uploads" / "sample.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("hello", encoding="utf-8")
    tool = FileStorageTool(storage=storage)
    binding = next(binding for binding in tool.bindings() if binding.tool.name == "self_insert_artifact")

    result = await binding.handler(
        {
            "path": "uploads/sample.txt",
            "as": "image",
        },
        ToolContext(owner_id="1", channel="telegram", chat_id=99, user_id=1),
    )

    assert isinstance(result, ToolResult)
    assert result.content["status"] == "error"
    assert result.content["code"] == "unsupported_mime"


@pytest.mark.asyncio
async def test_self_insert_artifact_rejects_absolute_paths(tmp_path: Path) -> None:
    root = tmp_path / "files"
    storage = LocalFileStorage(root_dir=str(root), max_write_bytes=1000)
    source = root / "uploads" / "sample.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"123")
    tool = FileStorageTool(storage=storage)
    binding = next(binding for binding in tool.bindings() if binding.tool.name == "self_insert_artifact")

    result = await binding.handler(
        {
            "path": str(source),
            "as": "image",
        },
        ToolContext(owner_id="1", channel="telegram", chat_id=99, user_id=1),
    )

    assert isinstance(result, ToolResult)
    assert result.content["status"] == "error"
    assert result.content["code"] == "invalid_path"
