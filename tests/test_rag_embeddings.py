from __future__ import annotations

import sys
from types import ModuleType

from minibot.rag import embeddings


def test_get_model_cache_key_includes_truncate_dim(monkeypatch) -> None:
    calls: list[tuple[str, int | None]] = []

    class _FakeSentenceTransformer:
        def __init__(self, model_name: str, **kwargs) -> None:
            calls.append((model_name, kwargs.get("truncate_dim")))

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = _FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setattr(embeddings, "_model_instance", None)
    monkeypatch.setattr(embeddings, "_model_key", None)

    first = embeddings._get_model("demo-model", 128)
    second = embeddings._get_model("demo-model", 128)
    third = embeddings._get_model("demo-model", 64)

    assert first is second
    assert third is not second
    assert calls == [("demo-model", 128), ("demo-model", 64)]
