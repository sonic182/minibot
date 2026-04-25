from __future__ import annotations

import sys
from types import ModuleType

import pytest

from minibot.rag import reranking


def test_get_reranker_raises_clear_error_when_cross_encoder_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = ModuleType("sentence_transformers")
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(reranking, "_reranker_instances", {})

    with pytest.raises(RuntimeError, match="CrossEncoder is required for RAG reranking"):
        reranking._get_reranker("demo-model")


def test_get_reranker_caches_by_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _FakeCrossEncoder:
        def __init__(self, model_name: str) -> None:
            calls.append(model_name)

    fake_module = ModuleType("sentence_transformers")
    fake_module.CrossEncoder = _FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(reranking, "_reranker_instances", {})

    first = reranking._get_reranker("demo-model")
    second = reranking._get_reranker("demo-model")
    third = reranking._get_reranker("other-model")

    assert first is second
    assert third is not second
    assert calls == ["demo-model", "other-model"]


def test_get_reranker_surfaces_model_load_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeCrossEncoder:
        def __init__(self, model_name: str) -> None:
            raise ValueError(f"bad model {model_name}")

    fake_module = ModuleType("sentence_transformers")
    fake_module.CrossEncoder = _FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(reranking, "_reranker_instances", {})

    with pytest.raises(RuntimeError, match="failed to load RAG reranker model 'broken-model': bad model broken-model"):
        reranking._get_reranker("broken-model")
