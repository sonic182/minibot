from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from minibot.adapters.files.local_storage import LocalFileStorage
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


@pytest.mark.asyncio
async def test_send_file_publishes_outbound_file_event(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="docs/report.txt", content="ok", overwrite=False)
    event_bus = _EventBusStub()
    tool = FileStorageTool(storage=storage, event_bus=event_bus)
    send_binding = next(binding for binding in tool.bindings() if binding.tool.name == "send_file")

    result = await send_binding.handler(
        {"path": "docs/report.txt", "caption": "latest"},
        ToolContext(owner_id="1", channel="telegram", chat_id=99, user_id=1),
    )

    assert result["ok"] is True
    assert len(event_bus.events) == 1
    outbound = event_bus.events[0]
    assert isinstance(outbound, OutboundFileEvent)
    assert outbound.response.chat_id == 99
    assert outbound.response.caption == "latest"
    assert Path(outbound.response.file_path).name == "report.txt"
