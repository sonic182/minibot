from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

_reranker_lock = threading.Lock()
_reranker_instances: dict[str, CrossEncoder] = {}


def _get_reranker(model_name: str) -> Any:
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers CrossEncoder is required for RAG reranking. "
            "Install the base project with `poetry install --all-extras`, then install "
            "`torch` and `sentence-transformers` manually."
        ) from exc

    with _reranker_lock:
        reranker = _reranker_instances.get(model_name)
        if reranker is None:
            try:
                reranker = CrossEncoder(model_name)
            except Exception as exc:  # pragma: no cover - exact loader failures depend on local env
                raise RuntimeError(f"failed to load RAG reranker model '{model_name}': {exc}") from exc
            _reranker_instances[model_name] = reranker
    return reranker


def _predict_scores_sync(model_name: str, query: str, texts: list[str]) -> list[float]:
    reranker = _get_reranker(model_name)
    pairs = [(query, text) for text in texts]
    raw_scores = reranker.predict(pairs)
    return [float(score) for score in raw_scores]


async def rerank_texts(model_name: str, query: str, texts: list[str]) -> list[float]:
    return await asyncio.to_thread(_predict_scores_sync, model_name, query, texts)
