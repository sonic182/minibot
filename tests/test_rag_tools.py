from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.qdrant.client import AsyncQdrantClient
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.rag_tools import RagTools


def _storage(root: Path, *, allow_outside_root: bool = False) -> LocalFileStorage:
    return LocalFileStorage(
        root_dir=str(root),
        max_write_bytes=10_000,
        allow_outside_root=allow_outside_root,
    )


def _tool(storage: LocalFileStorage) -> RagTools:
    return RagTools(
        config=SimpleNamespace(
            collection_name="chunks",
            chunk_size=800,
            chunk_overlap=120,
            search_limit=5,
            embedding=SimpleNamespace(model="mini", truncate_dim=None),
        ),
        qdrant=AsyncQdrantClient(url="http://example.com"),
        storage=storage,
    )


@pytest.mark.asyncio
async def test_rag_index_defaults_user_and_chat_scope_from_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _storage(tmp_path)
    storage.create_text_file("docs/report.txt", "hello", overwrite=False)
    tool = _tool(storage)
    binding = next(item for item in tool.bindings() if item.tool.name == "rag_index")
    captured: dict[str, Any] = {}

    async def _fake_index_document(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 1

    monkeypatch.setattr("minibot.llm.tools.rag_tools.index_document", _fake_index_document)

    result = await binding.handler(
        {"file_path": "docs/report.txt"},
        ToolContext(owner_id="owner", channel="telegram", chat_id=99, user_id=7),
    )

    assert result["chunks_indexed"] == 1
    assert captured["user_id"] == "7"
    assert captured["chat_id"] == "99"


@pytest.mark.asyncio
async def test_rag_index_rejects_outside_root_path_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    tool = _tool(_storage(root))
    binding = next(item for item in tool.bindings() if item.tool.name == "rag_index")
    called = False

    async def _fake_index_document(**kwargs: Any) -> int:
        nonlocal called
        called = True
        del kwargs
        return 1

    monkeypatch.setattr("minibot.llm.tools.rag_tools.index_document", _fake_index_document)

    with pytest.raises(ValueError, match="relative to managed root"):
        await binding.handler(
            {"file_path": str(outside.resolve())},
            ToolContext(owner_id="owner", channel="telegram", chat_id=99, user_id=7),
        )

    assert called is False


@pytest.mark.asyncio
async def test_rag_index_allows_absolute_path_only_when_storage_allows_outside_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "shared.txt"
    outside.write_text("shared", encoding="utf-8")
    tool = _tool(_storage(root, allow_outside_root=True))
    binding = next(item for item in tool.bindings() if item.tool.name == "rag_index")
    captured: dict[str, Any] = {}

    async def _fake_index_document(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 1

    monkeypatch.setattr("minibot.llm.tools.rag_tools.index_document", _fake_index_document)

    await binding.handler(
        {"file_path": str(outside.resolve())},
        ToolContext(owner_id="owner", channel="telegram", chat_id=99, user_id=7),
    )

    assert captured["source_name"] == "shared.txt"


@pytest.mark.asyncio
async def test_rag_search_defaults_scope_from_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool = _tool(_storage(tmp_path))
    binding = next(item for item in tool.bindings() if item.tool.name == "rag_search")
    captured: dict[str, Any] = {}

    async def _fake_retrieve_context(**kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr("minibot.llm.tools.rag_tools.retrieve_context", _fake_retrieve_context)

    result = await binding.handler(
        {"query": "hello"},
        ToolContext(owner_id="owner", channel="telegram", chat_id=321, user_id=123),
    )

    assert result == {"results": []}
    assert captured["user_id"] == "123"
    assert captured["chat_id"] == "321"


@pytest.mark.asyncio
async def test_rag_delete_defaults_scope_from_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool = _tool(_storage(tmp_path))
    binding = next(item for item in tool.bindings() if item.tool.name == "rag_delete")
    captured: dict[str, Any] = {}

    async def _fake_delete_document(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("minibot.llm.tools.rag_tools.delete_document", _fake_delete_document)

    result = await binding.handler(
        {},
        ToolContext(owner_id="owner", channel="telegram", chat_id=456, user_id=123),
    )

    assert result == {"deleted": True}
    assert captured["user_id"] == "123"
    assert captured["chat_id"] == "456"
