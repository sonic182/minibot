from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_model_lock = threading.Lock()
_model_instance: SentenceTransformer | None = None
_model_name: str | None = None


def _get_model(model_name: str, truncate_dim: int | None) -> Any:
    global _model_instance, _model_name  # noqa: PLW0603

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError(
            "sentence-transformers is required for RAG. "
            "Install with `poetry install --extras rag` or `poetry install --all-extras`."
        ) from exc

    with _model_lock:
        if _model_instance is None or _model_name != model_name:
            kwargs: dict[str, Any] = {}
            if truncate_dim is not None:
                kwargs["truncate_dim"] = truncate_dim
            _model_instance = SentenceTransformer(model_name, **kwargs)
            _model_name = model_name
    return _model_instance


def _encode_sync(model_name: str, truncate_dim: int | None, texts: list[str]) -> list[list[float]]:
    model = _get_model(model_name, truncate_dim)
    vectors = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


async def embed_texts(model_name: str, truncate_dim: int | None, texts: list[str]) -> list[list[float]]:
    return await asyncio.to_thread(_encode_sync, model_name, truncate_dim, texts)


async def embed_text(model_name: str, truncate_dim: int | None, text: str) -> list[float]:
    results = await embed_texts(model_name, truncate_dim, [text])
    return results[0]
