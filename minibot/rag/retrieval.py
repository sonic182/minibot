from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from minibot.adapters.qdrant.client import AsyncQdrantClient
from minibot.rag.chunking import chunk_text
from minibot.rag.embeddings import embed_text, embed_texts
from minibot.rag.reranking import rerank_texts

_logger = logging.getLogger("minibot.rag.retrieval")


async def index_document(
    *,
    client: AsyncQdrantClient,
    collection: str,
    document_id: str,
    text: str,
    filename: str,
    source_type: str = "file",
    mime_type: str = "text/plain",
    user_id: str | None = None,
    agent_id: str | None = None,
    chat_id: str | None = None,
    tags: list[str] | None = None,
    categories: list[str] | None = None,
    chunk_size_tokens: int = 96,
    chunk_overlap_tokens: int = 20,
    embedding_model: str = "sentence-transformers/all-MiniLM-L12-v2",
    truncate_dim: int | None = None,
) -> int:
    raw_chunks = await asyncio.to_thread(
        chunk_text,
        text,
        chunk_size_tokens=chunk_size_tokens,
        overlap_tokens=chunk_overlap_tokens,
        embedding_model=embedding_model,
        truncate_dim=truncate_dim,
    )
    await delete_document(
        client=client,
        collection=collection,
        document_id=document_id,
        user_id=user_id,
        agent_id=agent_id,
        chat_id=chat_id,
    )
    if not raw_chunks:
        return 0

    vectors = await embed_texts(embedding_model, truncate_dim, raw_chunks)

    payload_base: dict[str, Any] = {
        "document_id": document_id,
        "filename": filename,
        "source_type": source_type,
        "mime_type": mime_type,
        "user_id": user_id,
        "agent_id": agent_id,
        "chat_id": chat_id,
        "tags": tags,
        "categories": categories,
    }

    points = [
        {
            "id": _build_chunk_id(
                document_id=document_id,
                user_id=user_id,
                agent_id=agent_id,
                chat_id=chat_id,
                chunk_index=idx,
            ),
            "vector": vectors[idx],
            "payload": {
                **payload_base,
                "chunk_id": _build_chunk_id(
                    document_id=document_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    chat_id=chat_id,
                    chunk_index=idx,
                ),
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
    tags: list[str] | None = None,
    categories: list[str] | None = None,
    filename: str | None = None,
) -> None:
    filters = _build_filters(
        document_id=document_id,
        user_id=user_id,
        agent_id=agent_id,
        chat_id=chat_id,
        tags=tags,
        categories=categories,
        filename=filename,
    )
    if filters is None:
        raise ValueError("at least one filter is required for delete")
    await client.delete_by_filter(collection, filters)


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
    tags: list[str] | None = None,
    categories: list[str] | None = None,
    filename: str | None = None,
    embedding_model: str = "sentence-transformers/all-MiniLM-L12-v2",
    truncate_dim: int | None = None,
    rerank_enabled: bool = False,
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L2-v2",
    rerank_candidate_limit: int = 50,
    rerank_max_results: int = 7,
) -> list[dict[str, Any]]:
    vector = await embed_text(embedding_model, truncate_dim, query)
    filters = _build_filters(
        document_id=document_id,
        user_id=user_id,
        agent_id=agent_id,
        chat_id=chat_id,
        tags=tags,
        categories=categories,
        filename=filename,
    )

    if not rerank_enabled:
        results = await client.search(collection, vector, limit=limit, filters=filters)
        _logger.debug(
            "rag semantic search completed",
            extra={"limit": limit, "results_count": len(results), "rerank_enabled": False},
        )
        return [
            {
                "score": r["score"],
                "text": r["payload"].get("text", ""),
                "metadata": {k: v for k, v in r["payload"].items() if k != "text"},
            }
            for r in results
        ]

    effective_final_limit = min(limit, rerank_max_results)
    semantic_candidate_limit = max(effective_final_limit, rerank_candidate_limit)
    results = await client.search(collection, vector, limit=semantic_candidate_limit, filters=filters)
    _logger.info(
        "rag rerank candidate search completed",
        extra={
            "requested_final_limit": limit,
            "effective_final_limit": effective_final_limit,
            "semantic_candidate_limit": semantic_candidate_limit,
            "candidate_count": len(results),
            "rerank_model": rerank_model,
        },
    )
    if len(results) < 2:
        _logger.info(
            "rag rerank skipped",
            extra={
                "reason": "candidate_count_lt_2",
                "candidate_count": len(results),
                "effective_final_limit": effective_final_limit,
                "rerank_model": rerank_model,
            },
        )
        return [
            {
                "score": r["score"],
                "text": r["payload"].get("text", ""),
                "metadata": {k: v for k, v in r["payload"].items() if k != "text"},
            }
            for r in results[:effective_final_limit]
        ]

    texts = [result["payload"].get("text", "") for result in results]
    rerank_scores = await rerank_texts(rerank_model, query, texts)
    ranked_pairs = sorted(zip(results, rerank_scores, strict=True), key=lambda item: item[1], reverse=True)
    _logger.info(
        "rag rerank applied",
        extra={
            "candidate_count": len(results),
            "returned_count": min(len(ranked_pairs), effective_final_limit),
            "effective_final_limit": effective_final_limit,
            "rerank_model": rerank_model,
        },
    )
    return [
        {
            "score": rerank_score,
            "semantic_score": result["score"],
            "text": result["payload"].get("text", ""),
            "metadata": {k: v for k, v in result["payload"].items() if k != "text"},
        }
        for result, rerank_score in ranked_pairs[:effective_final_limit]
    ]


async def list_metadata_facets(
    *,
    client: AsyncQdrantClient,
    collection: str,
    limit: int = 10,
    document_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    chat_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    filters = _build_filters(
        document_id=document_id,
        user_id=user_id,
        agent_id=agent_id,
        chat_id=chat_id,
    )
    tags, categories, filenames = await asyncio.gather(
        client.facet(collection, key="tags", limit=limit, filters=filters),
        client.facet(collection, key="categories", limit=limit, filters=filters),
        client.facet(collection, key="filename", limit=limit, filters=filters),
    )
    return {"tags": tags, "categories": categories, "filenames": filenames}


def _build_filters(
    *,
    document_id: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    chat_id: str | None = None,
    tags: list[str] | None = None,
    categories: list[str] | None = None,
    filename: str | None = None,
) -> dict[str, Any] | None:
    conditions: list[dict[str, Any]] = []
    if document_id:
        conditions.append({"key": "document_id", "match": {"value": document_id}})
    if user_id:
        conditions.append({"key": "user_id", "match": {"value": user_id}})
    if agent_id:
        conditions.append({"key": "agent_id", "match": {"value": agent_id}})
    if chat_id:
        conditions.append({"key": "chat_id", "match": {"value": chat_id}})
    if tags:
        conditions.append({"key": "tags", "match": {"any": tags}})
    if categories:
        conditions.append({"key": "categories", "match": {"any": categories}})
    if filename:
        conditions.append({"key": "filename", "match": {"value": filename}})
    if not conditions:
        return None
    return {"must": conditions}


def _build_chunk_id(
    *,
    document_id: str,
    user_id: str | None,
    agent_id: str | None,
    chat_id: str | None,
    chunk_index: int,
) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_DNS,
            ":".join(
                [
                    document_id,
                    user_id or "",
                    agent_id or "",
                    chat_id or "",
                    str(chunk_index),
                ]
            ),
        )
    )
