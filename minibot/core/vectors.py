from __future__ import annotations

from typing import Any, Protocol

# The filter dict is Qdrant's shape, narrowed to what `rag/retrieval.py:_build_filters` actually
# emits — a single `must` list of `{"key": str, "match": {"value": str} | {"any": [str]}}`. Keeping
# it avoids rewriting the filter builder, its four callers and the Qdrant client for two backends.
# A third backend needing ranges or nested OR is the point to promote this to a typed filter here.


class VectorStore(Protocol):
    """Chunk storage and vector search behind ``[tools.rag].backend``.

    Implemented by ``adapters/qdrant/client.py`` (HTTP) and ``adapters/vectors/sqlite.py`` (local).
    """

    async def ensure_collection(self, collection_name: str, vector_size: int) -> None: ...

    async def create_payload_index(
        self, collection_name: str, field_name: str, field_schema: str = "keyword"
    ) -> None: ...

    async def upsert_points(self, collection_name: str, points: list[dict[str, Any]]) -> None: ...

    async def delete_by_filter(self, collection_name: str, filters: dict[str, Any]) -> None: ...

    async def search(
        self,
        collection_name: str,
        vector: list[float],
        *,
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    async def facet(
        self,
        collection_name: str,
        *,
        key: str,
        limit: int,
        filters: dict[str, Any] | None = None,
        exact: bool = False,
    ) -> list[dict[str, Any]]: ...
