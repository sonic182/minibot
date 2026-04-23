from __future__ import annotations

import uuid
from typing import Any

from minibot.adapters.qdrant.client import AsyncQdrantClient
from minibot.rag.chunking import chunk_text
from minibot.rag.embeddings import embed_text, embed_texts


async def index_document(
    *,
    client: AsyncQdrantClient,
    collection: str,
    document_id: str,
    text: str,
    source_name: str,
    source_type: str = "file",
    mime_type: str = "text/plain",
    user_id: str | None = None,
    agent_id: str | None = None,
    chat_id: str | None = None,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
    embedding_model: str = "sentence-transformers/all-MiniLM-L12-v2",
    truncate_dim: int | None = None,
) -> int:
    raw_chunks = chunk_text(text, chunk_size=chunk_size, overlap=chunk_overlap)
    if not raw_chunks:
        return 0

    vectors = await embed_texts(embedding_model, truncate_dim, raw_chunks)

    payload_base: dict[str, Any] = {
        "document_id": document_id,
        "source_name": source_name,
        "source_type": source_type,
        "mime_type": mime_type,
        "user_id": user_id,
        "agent_id": agent_id,
        "chat_id": chat_id,
    }

    points = [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{document_id}:{idx}")),
            "vector": vectors[idx],
            "payload": {
                **payload_base,
                "chunk_id": f"{document_id}:{idx}",
                "chunk_index": idx,
                "text": chunk,
            },
        }
        for idx, chunk in enumerate(raw_chunks)
    ]

    await client.upsert_points(collection, points)
    return len(points)


async def delete_document(
    *,
    client: AsyncQdrantClient,
    collection: str,
    document_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    chat_id: str | None = None,
) -> None:
    conditions: list[dict[str, Any]] = []
    if document_id:
        conditions.append({"key": "document_id", "match": {"value": document_id}})
    if user_id:
        conditions.append({"key": "user_id", "match": {"value": user_id}})
    if agent_id:
        conditions.append({"key": "agent_id", "match": {"value": agent_id}})
    if chat_id:
        conditions.append({"key": "chat_id", "match": {"value": chat_id}})
    if not conditions:
        raise ValueError("at least one of document_id, user_id, agent_id, or chat_id is required")
    await client.delete_by_filter(collection, {"must": conditions})


async def retrieve_context(
    *,
    client: AsyncQdrantClient,
    collection: str,
    query: str,
    limit: int = 5,
    document_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    chat_id: str | None = None,
    embedding_model: str = "sentence-transformers/all-MiniLM-L12-v2",
    truncate_dim: int | None = None,
) -> list[dict[str, Any]]:
    vector = await embed_text(embedding_model, truncate_dim, query)

    conditions: list[dict[str, Any]] = []
    if document_id:
        conditions.append({"key": "document_id", "match": {"value": document_id}})
    if user_id:
        conditions.append({"key": "user_id", "match": {"value": user_id}})
    if agent_id:
        conditions.append({"key": "agent_id", "match": {"value": agent_id}})
    if chat_id:
        conditions.append({"key": "chat_id", "match": {"value": chat_id}})

    filters = {"must": conditions} if conditions else None

    results = await client.search(collection, vector, limit=limit, filters=filters)
    return [
        {
            "score": r["score"],
            "text": r["payload"].get("text", ""),
            "metadata": {k: v for k, v in r["payload"].items() if k != "text"},
        }
        for r in results
    ]
