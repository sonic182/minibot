from __future__ import annotations

from pathlib import Path

import pytest

from minibot.adapters.config.schema import FileStorageToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.file_storage import FileStorageTool


def _bindings(storage: LocalFileStorage) -> dict[str, ToolBinding]:
    return {binding.tool.name: binding for binding in FileStorageTool(storage).bindings()}


def _storage(tmp_path: Path, **kwargs: object) -> LocalFileStorage:
    config = FileStorageToolConfig(root_dir=str(tmp_path / "files"), **kwargs)
    return LocalFileStorage(config)


@pytest.mark.asyncio
async def test_file_write_and_read_lines(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    tools = _bindings(storage)
    context = ToolContext(owner_id="owner", channel="telegram", chat_id=100, user_id=200)

    write_result = await tools["file_write"].handler(
        {"path": "notes/today.txt", "content": "line1\nline2\nline3", "source": None},
        context,
    )

    assert write_result["ok"] is True
    assert write_result["file"]["relative_path"] == "notes/today.txt"
    assert write_result["file"]["source"] == "manual"

    read_result = await tools["file_read"].handler(
        {"path": "notes/today.txt", "mode": "lines", "offset": 1, "limit": 1},
        context,
    )

    assert read_result["ok"] is True
    assert read_result["content"] == "line2"
    assert read_result["has_more"] is True


@pytest.mark.asyncio
async def test_file_read_bytes_mode(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    tools = _bindings(storage)
    context = ToolContext(owner_id="owner")

    await tools["file_write"].handler(
        {"path": "bytes.txt", "content": "abcdef", "source": "manual"},
        context,
    )
    read_result = await tools["file_read"].handler(
        {"path": "bytes.txt", "mode": "bytes", "offset": 2, "limit": 2},
        context,
    )

    assert read_result["mode"] == "bytes"
    assert read_result["content"] == "cd"
    assert read_result["bytes_read"] == 2
    assert read_result["has_more"] is True


@pytest.mark.asyncio
async def test_file_list_pagination_and_prefix(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    tools = _bindings(storage)
    context = ToolContext(owner_id="owner")

    await tools["file_write"].handler({"path": "a/one.txt", "content": "1", "source": None}, context)
    await tools["file_write"].handler({"path": "a/two.txt", "content": "2", "source": None}, context)
    await tools["file_write"].handler({"path": "b/three.txt", "content": "3", "source": None}, context)

    result = await tools["file_list"].handler({"prefix": "a", "limit": 1, "offset": 1}, context)

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["files"][0]["relative_path"] == "a/two.txt"


@pytest.mark.asyncio
async def test_file_write_rejects_large_content(tmp_path: Path) -> None:
    storage = _storage(tmp_path, max_write_bytes=4)
    tools = _bindings(storage)

    with pytest.raises(ValueError):
        await tools["file_write"].handler(
            {"path": "too_big.txt", "content": "hello", "source": "manual"},
            ToolContext(),
        )


@pytest.mark.asyncio
async def test_file_write_rejects_path_traversal(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    tools = _bindings(storage)

    with pytest.raises(ValueError):
        await tools["file_write"].handler(
            {"path": "../escape.txt", "content": "no", "source": "manual"},
            ToolContext(),
        )
