from __future__ import annotations

from typing import Any

import pytest

from minibot.adapters.config.schema import RagToolConfig
from minibot.adapters.vectors.sqlite import SqliteVectorStore
from minibot.extensions.integrations import rag as rag_extension
from minibot.rag import retrieval

COLLECTION = "test_chunks"


async def _store(tmp_path: Any, *, vector_size: int = 2) -> SqliteVectorStore:
    config = RagToolConfig(enabled=True, sqlite_url=f"sqlite+aiosqlite:///{tmp_path}/rag.db")
    store = SqliteVectorStore(config)
    await store.ensure_collection(COLLECTION, vector_size)
    return store


def _point(point_id: str, vector: list[float], **payload: Any) -> dict[str, Any]:
    return {"id": point_id, "vector": vector, "payload": {"text": point_id, **payload}}


def _match(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "match": {"value": value}}


def _match_any(key: str, values: list[str]) -> dict[str, Any]:
    return {"key": key, "match": {"any": values}}


@pytest.mark.asyncio
async def test_search_ranks_by_similarity(tmp_path):
    store = await _store(tmp_path)
    await store.upsert_points(
        COLLECTION,
        [
            _point("east", [1.0, 0.0]),
            _point("north", [0.0, 1.0]),
            _point("northeast", [0.7071, 0.7071]),
        ],
    )

    results = await store.search(COLLECTION, [1.0, 0.0], limit=3)

    assert [r["payload"]["text"] for r in results] == ["east", "northeast", "north"]
    assert results[0]["score"] == pytest.approx(1.0)
    assert results[2]["score"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_search_limit_truncates_to_best_matches(tmp_path):
    store = await _store(tmp_path)
    await store.upsert_points(
        COLLECTION,
        [_point("east", [1.0, 0.0]), _point("north", [0.0, 1.0]), _point("northeast", [0.7071, 0.7071])],
    )

    results = await store.search(COLLECTION, [1.0, 0.0], limit=2)

    assert [r["payload"]["text"] for r in results] == ["east", "northeast"]


@pytest.mark.asyncio
async def test_search_applies_scope_filter(tmp_path):
    store = await _store(tmp_path)
    await store.upsert_points(
        COLLECTION,
        [
            _point("mine", [1.0, 0.0], user_id="u1", agent_id="a1"),
            _point("theirs", [1.0, 0.0], user_id="u2", agent_id="a1"),
        ],
    )

    results = await store.search(COLLECTION, [1.0, 0.0], limit=10, filters={"must": [_match("user_id", "u1")]})

    assert [r["payload"]["text"] for r in results] == ["mine"]


@pytest.mark.asyncio
async def test_search_filters_list_valued_payload_fields(tmp_path):
    store = await _store(tmp_path)
    await store.upsert_points(
        COLLECTION,
        [
            _point("recipes", [1.0, 0.0], tags=["food", "notes"]),
            _point("invoices", [1.0, 0.0], tags=["work"]),
        ],
    )

    results = await store.search(
        COLLECTION, [1.0, 0.0], limit=10, filters={"must": [_match_any("tags", ["food", "travel"])]}
    )

    assert [r["payload"]["text"] for r in results] == ["recipes"]


@pytest.mark.asyncio
async def test_upsert_replaces_existing_point(tmp_path):
    store = await _store(tmp_path)
    await store.upsert_points(COLLECTION, [_point("chunk", [1.0, 0.0], filename="old.txt")])
    await store.upsert_points(COLLECTION, [_point("chunk", [0.0, 1.0], filename="new.txt")])

    results = await store.search(COLLECTION, [0.0, 1.0], limit=10)

    assert len(results) == 1
    assert results[0]["payload"]["filename"] == "new.txt"
    assert results[0]["score"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_delete_by_filter_removes_only_matching_rows(tmp_path):
    store = await _store(tmp_path)
    await store.upsert_points(
        COLLECTION,
        [
            _point("keep", [1.0, 0.0], document_id="d1"),
            _point("drop", [1.0, 0.0], document_id="d2"),
        ],
    )

    await store.delete_by_filter(COLLECTION, {"must": [_match("document_id", "d2")]})
    results = await store.search(COLLECTION, [1.0, 0.0], limit=10)

    assert [r["payload"]["text"] for r in results] == ["keep"]


@pytest.mark.asyncio
async def test_facet_counts_scalar_and_list_values(tmp_path):
    store = await _store(tmp_path)
    await store.upsert_points(
        COLLECTION,
        [
            _point("a", [1.0, 0.0], filename="notes.md", tags=["food", "work"]),
            _point("b", [1.0, 0.0], filename="notes.md", tags=["work"]),
            _point("c", [1.0, 0.0], filename="other.md", tags=["work"]),
        ],
    )

    filenames = await store.facet(COLLECTION, key="filename", limit=10)
    tags = await store.facet(COLLECTION, key="tags", limit=1)

    assert filenames == [{"value": "notes.md", "count": 2}, {"value": "other.md", "count": 1}]
    assert tags == [{"value": "work", "count": 3}]


@pytest.mark.asyncio
async def test_ensure_collection_rejects_vector_size_change(tmp_path):
    store = await _store(tmp_path, vector_size=2)

    with pytest.raises(ValueError, match="vector size 2, expected 384"):
        await store.ensure_collection(COLLECTION, 384)


@pytest.mark.asyncio
async def test_unknown_exact_match_key_is_rejected(tmp_path):
    store = await _store(tmp_path)

    with pytest.raises(ValueError, match="unsupported rag filter key"):
        await store.search(COLLECTION, [1.0, 0.0], limit=10, filters={"must": [_match("text", "anything")]})


@pytest.mark.asyncio
async def test_retrieval_round_trip_over_the_sqlite_store(tmp_path, monkeypatch, numeric_tokenizer):
    """`retrieval.py` drives the store through the real point/payload shapes it builds."""
    config = RagToolConfig(enabled=True, sqlite_url=f"sqlite+aiosqlite:///{tmp_path}/rag.db")
    store = SqliteVectorStore(config)
    await rag_extension._RagService(config, store).start()

    # Each chunk embeds to a distinct axis so the query can single one out. Looking the vector up by
    # chunk text also pins down what the chunker handed over — an unexpected chunk raises KeyError.
    axes = {"1 2 3": [1.0, 0.0], "4 5 6": [0.0, 1.0]}
    monkeypatch.setattr(retrieval, "embed_texts", lambda _model, _dim, texts: _resolved([axes[t] for t in texts]))
    monkeypatch.setattr(retrieval, "embed_text", lambda _model, _dim, _query: _resolved([0.0, 1.0]))

    indexed = await retrieval.index_document(
        client=store,
        collection=config.collection_name,
        document_id="doc-1",
        text="1 2 3 4 5 6",
        filename="notes.md",
        user_id="u1",
        tags=["notes"],
        chunk_size_tokens=3,
        chunk_overlap_tokens=0,
    )
    assert indexed == 2

    results = await retrieval.retrieve_context(
        client=store,
        collection=config.collection_name,
        query="anything",
        limit=1,
        user_id="u1",
        tags=["notes"],
    )

    assert [r["text"] for r in results] == ["4 5 6"]
    assert results[0]["metadata"]["filename"] == "notes.md"
    assert results[0]["score"] == pytest.approx(1.0)

    facets = await retrieval.list_metadata_facets(client=store, collection=config.collection_name, user_id="u1")
    assert facets["filenames"] == [{"value": "notes.md", "count": 2}]

    await retrieval.delete_document(client=store, collection=config.collection_name, document_id="doc-1")
    assert await store.search(config.collection_name, [0.0, 1.0], limit=10) == []


async def _resolved(value: Any) -> Any:
    return value
