from __future__ import annotations

from pathlib import Path

import pytest

from minibot.adapters.config.schema import FileStorageToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.app.event_bus import EventBus
from minibot.core.events import OutboundMediaEvent
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.channel_file_sender import ChannelFileSenderTool


@pytest.mark.asyncio
async def test_send_file_in_channel_publishes_media_event(tmp_path: Path) -> None:
    storage = LocalFileStorage(FileStorageToolConfig(root_dir=str(tmp_path / "files")))
    record = await storage.write_text(
        path="report.txt",
        content="hello",
        owner_id="owner",
        channel="telegram",
        chat_id=1,
        user_id=2,
        source="manual",
    )
    assert record.relative_path == "report.txt"

    bus = EventBus()
    subscription = bus.subscribe()
    tool = ChannelFileSenderTool(bus, storage)
    binding = tool.bindings()[0]

    result = await binding.handler(
        {"path": "report.txt", "media_type": "document", "caption": "Report"},
        ToolContext(owner_id="owner", channel="telegram", chat_id=1, user_id=2),
    )
    event: OutboundMediaEvent | None = None
    async for pending_event in subscription:
        if isinstance(pending_event, OutboundMediaEvent):
            event = pending_event
            break
    await subscription.close()

    assert result["ok"] is True
    assert result["sent"] is True
    assert event is not None
    assert event.response.channel == "telegram"
    assert event.response.chat_id == 1
    assert event.response.media_type == "document"
    assert event.response.metadata["relative_path"] == "report.txt"


@pytest.mark.asyncio
async def test_send_file_in_channel_requires_chat_context(tmp_path: Path) -> None:
    storage = LocalFileStorage(FileStorageToolConfig(root_dir=str(tmp_path / "files")))
    await storage.write_text(
        path="photo.jpg",
        content="fake-image",
        owner_id=None,
        channel=None,
        chat_id=None,
        user_id=None,
        source="manual",
    )
    bus = EventBus()
    tool = ChannelFileSenderTool(bus, storage)
    binding = tool.bindings()[0]

    with pytest.raises(ValueError):
        await binding.handler(
            {"path": "photo.jpg", "media_type": "photo", "caption": None},
            ToolContext(channel="telegram", chat_id=None),
        )
