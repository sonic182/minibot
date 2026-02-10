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
    assert not (tmp_path / "archive" / "report.txt").exists()


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
