from __future__ import annotations

from typing import Any

import pytest

from minibot.rag import retrieval


def _search_result(*, text: str, score: float, document_id: str) -> dict[str, Any]:
    return {
        "score": score,
        "payload": {
            "text": text,
            "document_id": document_id,
            "filename": f"{document_id}.txt",
        },
    }


class _SearchClient:
    def __init__(self, results: list[dict[str, Any]], calls: dict[str, Any] | None = None) -> None:
        self._results = results
        self._calls = calls

    async def search(
        self,
        collection_name: str,
        vector: list[float],
        *,
        limit: int,
        filters: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if self._calls is not None:
            self._calls.update(
                collection_name=collection_name,
                vector=vector,
                limit=limit,
                filters=filters,
            )
        return self._results


def _patch_embed_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_embed_text(_model: str, _truncate_dim: int | None, _text: str) -> list[float]:
        return [0.1, 0.2]

    monkeypatch.setattr(retrieval, "embed_text", _fake_embed_text)


async def _retrieve(client: Any, **overrides: Any) -> list[dict[str, Any]]:
    kwargs: dict[str, Any] = {"collection": "chunks", "query": "hello"}
    kwargs.update(overrides)
    return await retrieval.retrieve_context(client=client, **kwargs)


@pytest.mark.asyncio
async def test_retrieve_context_preserves_semantic_only_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}
    client = _SearchClient(
        [
            _search_result(text="second", score=0.8, document_id="doc-2"),
            _search_result(text="first", score=0.9, document_id="doc-1"),
        ],
        calls,
    )
    _patch_embed_text(monkeypatch)

    result = await _retrieve(client, limit=2, user_id="user-1")

    assert calls["limit"] == 2
    assert result == [
        {
            "score": 0.8,
            "text": "second",
            "metadata": {"document_id": "doc-2", "filename": "doc-2.txt"},
        },
        {
            "score": 0.9,
            "text": "first",
            "metadata": {"document_id": "doc-1", "filename": "doc-1.txt"},
        },
    ]


@pytest.mark.asyncio
async def test_retrieve_context_reranks_larger_candidate_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}
    client = _SearchClient(
        [
            _search_result(text="alpha", score=0.91, document_id="doc-1"),
            _search_result(text="beta", score=0.88, document_id="doc-2"),
            _search_result(text="gamma", score=0.84, document_id="doc-3"),
        ],
        calls,
    )

    async def _fake_rerank_texts(model_name: str, query: str, texts: list[str]) -> list[float]:
        calls["rerank_model"] = model_name
        calls["rerank_query"] = query
        calls["rerank_texts"] = texts
        return [0.1, 0.9, 0.4]

    _patch_embed_text(monkeypatch)
    monkeypatch.setattr(retrieval, "rerank_texts", _fake_rerank_texts)

    result = await _retrieve(
        client,
        limit=2,
        rerank_enabled=True,
        rerank_model="cross-encoder/demo",
        rerank_candidate_limit=3,
        rerank_max_results=7,
    )

    assert calls["limit"] == 3
    assert calls["rerank_model"] == "cross-encoder/demo"
    assert result == [
        {
            "score": 0.9,
            "semantic_score": 0.88,
            "text": "beta",
            "metadata": {"document_id": "doc-2", "filename": "doc-2.txt"},
        },
        {
            "score": 0.4,
            "semantic_score": 0.84,
            "text": "gamma",
            "metadata": {"document_id": "doc-3", "filename": "doc-3.txt"},
        },
    ]


@pytest.mark.asyncio
async def test_retrieve_context_rerank_clamps_limit_to_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}
    client = _SearchClient(
        [
            _search_result(text="one", score=0.9, document_id="doc-1"),
            _search_result(text="two", score=0.8, document_id="doc-2"),
            _search_result(text="three", score=0.7, document_id="doc-3"),
            _search_result(text="four", score=0.6, document_id="doc-4"),
        ],
        calls,
    )

    async def _fake_rerank_texts(_model_name: str, _query: str, _texts: list[str]) -> list[float]:
        return [0.1, 0.4, 0.3, 0.2]

    _patch_embed_text(monkeypatch)
    monkeypatch.setattr(retrieval, "rerank_texts", _fake_rerank_texts)

    result = await _retrieve(
        client,
        limit=10,
        rerank_enabled=True,
        rerank_candidate_limit=2,
        rerank_max_results=3,
    )

    assert calls["limit"] == 3
    assert [item["text"] for item in result] == ["two", "three", "four"]


@pytest.mark.asyncio
async def test_retrieve_context_rerank_skips_for_zero_or_one_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"rerank": 0}

    async def _fake_rerank_texts(_model_name: str, _query: str, _texts: list[str]) -> list[float]:
        calls["rerank"] += 1
        return [0.9]

    _patch_embed_text(monkeypatch)
    monkeypatch.setattr(retrieval, "rerank_texts", _fake_rerank_texts)

    zero_result = await _retrieve(_SearchClient([]), limit=5, rerank_enabled=True)
    one_result = await _retrieve(
        _SearchClient([_search_result(text="only", score=0.95, document_id="doc-1")]),
        limit=5,
        rerank_enabled=True,
    )

    assert zero_result == []
    assert one_result == [
        {
            "score": 0.95,
            "text": "only",
            "metadata": {"document_id": "doc-1", "filename": "doc-1.txt"},
        }
    ]
    assert calls["rerank"] == 0


@pytest.mark.asyncio
async def test_retrieve_context_adds_filename_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}
    _patch_embed_text(monkeypatch)

    result = await _retrieve(_SearchClient([], calls), filename="notes.txt")

    assert result == []
    assert calls["filters"] == {"must": [{"key": "filename", "match": {"value": "notes.txt"}}]}
