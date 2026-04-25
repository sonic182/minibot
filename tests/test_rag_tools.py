from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.qdrant.client import AsyncQdrantClient
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.rag_tools import RagTools, _normalize_string_list, _truncate_search_results
from minibot.rag.document_ingestion import IndexableDocument
from minibot.rag.retrieval import _build_filters, index_document, list_metadata_facets


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
            chunk_size_tokens=96,
            chunk_overlap_tokens=20,
            search_limit=5,
            truncate_result_tokens=False,
            max_result_tokens=1500,
            embedding=SimpleNamespace(model="mini", truncate_dim=None),
            rerank=SimpleNamespace(
                enabled=False,
                model="cross-encoder/ms-marco-MiniLM-L2-v2",
                candidate_limit=50,
                max_results=7,
            ),
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
    assert captured["mime_type"] == "text/plain"


@pytest.mark.asyncio
async def test_rag_index_normalizes_tags_and_categories(
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

    await binding.handler(
        {
            "file_path": "docs/report.txt",
            "tags": [" Plan ", "plan", "", "Notes"],
            "categories": [" Work ", "work", "Docs"],
        },
        ToolContext(owner_id="owner", channel="telegram", chat_id=99, user_id=7),
    )

    assert captured["tags"] == ["plan", "notes"]
    assert captured["categories"] == ["work", "docs"]


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
async def test_rag_index_extracts_pdf_content_and_sets_pdf_mime_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _storage(tmp_path)
    pdf_path = tmp_path / "docs" / "report.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4")
    tool = _tool(storage)
    binding = next(item for item in tool.bindings() if item.tool.name == "rag_index")
    captured: dict[str, Any] = {}

    def _fake_extract(_path: Path) -> IndexableDocument:
        return IndexableDocument(text="[PAGE 1]\nhello pdf", mime_type="application/pdf")

    async def _fake_index_document(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 1

    monkeypatch.setattr("minibot.llm.tools.rag_tools.extract_indexable_document", _fake_extract)
    monkeypatch.setattr("minibot.llm.tools.rag_tools.index_document", _fake_index_document)

    result = await binding.handler(
        {"file_path": "docs/report.pdf"},
        ToolContext(owner_id="owner", channel="telegram", chat_id=99, user_id=7),
    )

    assert result["chunks_indexed"] == 1
    assert captured["text"] == "[PAGE 1]\nhello pdf"
    assert captured["mime_type"] == "application/pdf"


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

    assert result == {"results": [], "truncated": False, "truncated_tokens": 0}
    assert captured["user_id"] == "123"
    assert captured["chat_id"] == "321"
    assert captured["rerank_enabled"] is False
    assert captured["rerank_model"] == "cross-encoder/ms-marco-MiniLM-L2-v2"
    assert captured["rerank_candidate_limit"] == 50
    assert captured["rerank_max_results"] == 7


@pytest.mark.asyncio
async def test_rag_search_normalizes_metadata_filters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool = _tool(_storage(tmp_path))
    binding = next(item for item in tool.bindings() if item.tool.name == "rag_search")
    captured: dict[str, Any] = {}

    async def _fake_retrieve_context(**kwargs: Any) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr("minibot.llm.tools.rag_tools.retrieve_context", _fake_retrieve_context)

    await binding.handler(
        {"query": "hello", "tags": [" Alpha ", "alpha", "Beta"], "categories": ["Docs", " docs "]},
        ToolContext(owner_id="owner", channel="telegram", chat_id=321, user_id=123),
    )

    assert captured["tags"] == ["alpha", "beta"]
    assert captured["categories"] == ["docs"]


@pytest.mark.asyncio
async def test_rag_list_metadata_defaults_scope_from_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tool = _tool(_storage(tmp_path))
    binding = next(item for item in tool.bindings() if item.tool.name == "rag_list_metadata")
    captured: dict[str, Any] = {}

    async def _fake_list_metadata_facets(**kwargs: Any) -> dict[str, list[dict[str, Any]]]:
        captured.update(kwargs)
        return {"tags": [{"value": "plan", "count": 2}], "categories": [{"value": "docs", "count": 1}]}

    monkeypatch.setattr("minibot.llm.tools.rag_tools.list_metadata_facets", _fake_list_metadata_facets)

    result = await binding.handler(
        {},
        ToolContext(owner_id="owner", channel="telegram", chat_id=456, user_id=123),
    )

    assert result == {"tags": [{"value": "plan", "count": 2}], "categories": [{"value": "docs", "count": 1}]}
    assert captured["user_id"] == "123"
    assert captured["chat_id"] == "456"
    assert captured["limit"] == 5


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


@pytest.mark.asyncio
async def test_rag_delete_normalizes_metadata_filters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    tool = _tool(_storage(tmp_path))
    binding = next(item for item in tool.bindings() if item.tool.name == "rag_delete")
    captured: dict[str, Any] = {}

    async def _fake_delete_document(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("minibot.llm.tools.rag_tools.delete_document", _fake_delete_document)

    await binding.handler(
        {"tags": [" Alpha ", "alpha"], "categories": ["Docs", " docs "]},
        ToolContext(owner_id="owner", channel="telegram", chat_id=456, user_id=123),
    )

    assert captured["tags"] == ["alpha"]
    assert captured["categories"] == ["docs"]


@pytest.mark.asyncio
async def test_index_document_deletes_existing_chunks_before_upsert(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any] | list[dict[str, Any]]]] = []

    class _Client:
        async def delete_by_filter(self, collection_name: str, filters: dict[str, Any]) -> None:
            calls.append(("delete", {"collection_name": collection_name, "filters": filters}))

        async def upsert_points(self, collection_name: str, points: list[dict[str, Any]]) -> None:
            calls.append(("upsert", [{"collection_name": collection_name, "count": len(points)}]))

    async def _fake_embed_texts(_model_name: str, _truncate_dim: int | None, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]

    def _fake_chunk_text(*args: Any, **kwargs: Any) -> list[str]:
        del args, kwargs
        return ["hello world"]

    monkeypatch.setattr("minibot.rag.retrieval.embed_texts", _fake_embed_texts)
    monkeypatch.setattr("minibot.rag.retrieval.chunk_text", _fake_chunk_text)

    result = await index_document(
        client=_Client(),  # type: ignore[arg-type]
        collection="chunks",
        document_id="doc-1",
        text="hello world",
        source_name="notes.txt",
        user_id="user-1",
        chat_id="chat-1",
        embedding_model="mini",
    )

    assert result == 1
    assert calls[0] == (
        "delete",
        {
            "collection_name": "chunks",
            "filters": {
                "must": [
                    {"key": "document_id", "match": {"value": "doc-1"}},
                    {"key": "user_id", "match": {"value": "user-1"}},
                    {"key": "chat_id", "match": {"value": "chat-1"}},
                ]
            },
        },
    )
    assert calls[1] == ("upsert", [{"collection_name": "chunks", "count": 1}])


@pytest.mark.asyncio
async def test_index_document_deletes_existing_chunks_for_empty_reindex() -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    class _Client:
        async def delete_by_filter(self, collection_name: str, filters: dict[str, Any]) -> None:
            calls.append(("delete", collection_name, filters))

        async def upsert_points(self, collection_name: str, points: list[dict[str, Any]]) -> None:
            calls.append(("upsert", collection_name, {"count": len(points)}))

    result = await index_document(
        client=_Client(),  # type: ignore[arg-type]
        collection="chunks",
        document_id="doc-1",
        text="   ",
        source_name="notes.txt",
    )

    assert result == 0
    assert calls == [
        (
            "delete",
            "chunks",
            {"must": [{"key": "document_id", "match": {"value": "doc-1"}}]},
        )
    ]


def test_normalize_string_list_rejects_non_list() -> None:
    with pytest.raises(ValueError, match="tags must be a list of strings"):
        _normalize_string_list("plan", field="tags")


def test_normalize_string_list_rejects_non_string_items() -> None:
    with pytest.raises(ValueError, match="categories must be a list of strings"):
        _normalize_string_list(["docs", 1], field="categories")


def test_normalize_string_list_returns_none_for_empty_normalized_values() -> None:
    assert _normalize_string_list(["", "   "], field="tags") is None


def test_truncate_search_results_uses_token_budget(numeric_tokenizer: MagicMock) -> None:
    results, truncated, truncated_tokens = _truncate_search_results(
        [
            {"text": "1 2 3", "score": 0.9},
            {"text": "4 5 6 7", "score": 0.8},
        ],
        max_tokens=5,
        embedding_model="mini",
        truncate_dim=None,
    )

    assert truncated is True
    assert truncated_tokens == 2
    assert results == [
        {"text": "1 2 3", "score": 0.9},
        {"text": "4 5\n...[truncated 2 tokens]", "score": 0.8},
    ]
    assert numeric_tokenizer.call_count == 5
    numeric_tokenizer.decode.assert_called_once()


def test_build_filters_combines_scalar_and_match_any_list_filters() -> None:
    filters = _build_filters(
        document_id="doc-1",
        user_id="user-1",
        chat_id="chat-1",
        tags=["alpha", "beta"],
        categories=["docs"],
    )

    assert filters == {
        "must": [
            {"key": "document_id", "match": {"value": "doc-1"}},
            {"key": "user_id", "match": {"value": "user-1"}},
            {"key": "chat_id", "match": {"value": "chat-1"}},
            {"key": "tags", "match": {"any": ["alpha", "beta"]}},
            {"key": "categories", "match": {"any": ["docs"]}},
        ]
    }


def test_build_filters_returns_none_without_conditions() -> None:
    assert _build_filters() is None


@pytest.mark.asyncio
async def test_list_metadata_facets_requests_tags_and_categories_in_parallel(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class _Client:
        async def facet(
            self,
            collection_name: str,
            *,
            key: str,
            limit: int,
            filters: dict[str, Any] | None = None,
            exact: bool = False,
        ) -> list[dict[str, Any]]:
            calls.append(
                {
                    "collection_name": collection_name,
                    "key": key,
                    "limit": limit,
                    "filters": filters,
                    "exact": exact,
                }
            )
            return [{"value": f"{key}-value", "count": 1}]

    result = await list_metadata_facets(
        client=_Client(),  # type: ignore[arg-type]
        collection="chunks",
        limit=7,
        user_id="user-1",
        chat_id="chat-1",
    )

    assert result == {
        "tags": [{"value": "tags-value", "count": 1}],
        "categories": [{"value": "categories-value", "count": 1}],
    }
    assert calls == [
        {
            "collection_name": "chunks",
            "key": "tags",
            "limit": 7,
            "filters": {
                "must": [
                    {"key": "user_id", "match": {"value": "user-1"}},
                    {"key": "chat_id", "match": {"value": "chat-1"}},
                ]
            },
            "exact": False,
        },
        {
            "collection_name": "chunks",
            "key": "categories",
            "limit": 7,
            "filters": {
                "must": [
                    {"key": "user_id", "match": {"value": "user-1"}},
                    {"key": "chat_id", "match": {"value": "chat-1"}},
                ]
            },
            "exact": False,
        },
    ]
