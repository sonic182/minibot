from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minibot.adapters.config.schema import GrepToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.grep import GrepTool


def _grep_binding(tool: GrepTool):
    return next(binding for binding in tool.bindings() if binding.tool.name == "grep")


@pytest.mark.asyncio
async def test_grep_tool_finds_matches_recursively(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="docs/a.txt", content="hello\nworld", overwrite=False)
    storage.create_text_file(path="docs/sub/b.txt", content="HELLO", overwrite=False)
    tool = GrepTool(storage=storage, config=GrepToolConfig(enabled=True, max_matches=10))
    binding = _grep_binding(tool)

    result = await binding.handler(
        {"pattern": "hello", "path": "docs", "ignore_case": True},
        ToolContext(owner_id="1", channel="telegram", chat_id=1, user_id=1),
    )

    assert isinstance(result, dict)
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["truncated"] is False
    assert len(result["matches"]) == 2


@pytest.mark.asyncio
async def test_grep_tool_respects_max_matches(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="a.txt", content="x\nx\nx", overwrite=False)
    tool = GrepTool(storage=storage, config=GrepToolConfig(enabled=True, max_matches=2))
    binding = _grep_binding(tool)

    result = await binding.handler({"pattern": "x", "path": "a.txt"}, ToolContext())

    assert isinstance(result, dict)
    assert result["count"] == 2
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_grep_tool_supports_fixed_string(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="log.txt", content="a+b\naxb", overwrite=False)
    tool = GrepTool(storage=storage, config=GrepToolConfig(enabled=True))
    binding = _grep_binding(tool)

    result = await binding.handler(
        {"pattern": "a+b", "path": "log.txt", "fixed_string": True},
        ToolContext(),
    )

    assert isinstance(result, dict)
    assert result["count"] == 1
    assert result["matches"][0]["line"] == 1


@pytest.mark.asyncio
async def test_grep_tool_rejects_invalid_regex(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="file.txt", content="abc", overwrite=False)
    tool = GrepTool(storage=storage, config=GrepToolConfig(enabled=True))
    binding = _grep_binding(tool)

    with pytest.raises(ValueError, match="invalid regex pattern"):
        await binding.handler({"pattern": "(", "path": "file.txt"}, ToolContext())


@pytest.mark.asyncio
async def test_grep_tool_skips_large_files(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=20000)
    small = "needle\nok"
    large = "a" * 200
    storage.create_text_file(path="small.txt", content=small, overwrite=False)
    storage.create_text_file(path="large.txt", content=large, overwrite=False)
    tool = GrepTool(storage=storage, config=GrepToolConfig(enabled=True, max_file_size_bytes=50))
    binding = _grep_binding(tool)

    result = await binding.handler({"pattern": "needle", "path": "."}, ToolContext())

    assert isinstance(result, dict)
    assert result["count"] == 1
    assert result["files_skipped"] >= 1


@pytest.mark.asyncio
async def test_grep_tool_excludes_hidden_files_by_default(tmp_path: Path) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="visible.txt", content="token", overwrite=False)
    storage.create_text_file(path=".env", content="token", overwrite=False)
    tool = GrepTool(storage=storage, config=GrepToolConfig(enabled=True, max_matches=10))
    binding = _grep_binding(tool)

    result = await binding.handler({"pattern": "token", "path": "."}, ToolContext())

    assert isinstance(result, dict)
    assert result["count"] == 1
    assert result["matches"][0]["path"] == "visible.txt"


@pytest.mark.asyncio
async def test_grep_tool_offloads_search_work_with_to_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1000)
    storage.create_text_file(path="a.txt", content="needle", overwrite=False)
    tool = GrepTool(storage=storage, config=GrepToolConfig(enabled=True))
    binding = _grep_binding(tool)
    calls: list[str] = []

    async def _fake_to_thread(func, /, *args, **kwargs):
        calls.append(getattr(func, "__name__", "unknown"))
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)
    result = await binding.handler({"pattern": "needle", "path": "a.txt"}, ToolContext())

    assert result["ok"] is True
    assert calls == ["_search_files"]
