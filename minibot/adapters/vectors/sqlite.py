from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import numpy as np
from sqlalchemy import JSON, Index, Integer, LargeBinary, String, delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from minibot.adapters.config.schema import RagToolConfig
from minibot.adapters.sqlalchemy_utils import ensure_parent_dir, resolve_sqlite_storage_path

# Each store module owns its declarative base so create_all only touches its own tables.
RagBase = declarative_base()

# Payload keys promoted to indexed columns; every other key stays inside the JSON payload.
_SCALAR_KEYS = ("document_id", "user_id", "agent_id", "chat_id", "filename")


class RagChunk(RagBase):
    __tablename__ = "rag_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    collection: Mapped[str] = mapped_column(String(128), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_rag_chunks_scope", "collection", "user_id", "agent_id", "chat_id"),
        Index("ix_rag_chunks_document", "collection", "document_id"),
    )


class RagCollection(RagBase):
    __tablename__ = "rag_collections"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    vector_size: Mapped[int] = mapped_column(Integer, nullable=False)


class SqliteVectorStore:
    """Local vector store backing ``tools.rag.backend = "sqlite"``.

    Scope filters (``user_id``/``agent_id``/``chat_id``/``document_id``/``filename``) run in SQL
    against indexed columns, so the similarity scan only ever touches the surviving rows. Scores are
    plain dot products: embeddings arrive normalized from ``rag/embeddings.py``, which makes the dot
    product the cosine similarity. Search is exact — there is no approximate index to lose recall to.
    """

    def __init__(self, config: RagToolConfig) -> None:
        self._database_url = make_url(config.sqlite_url)
        storage_path = resolve_sqlite_storage_path(config.sqlite_url)
        if storage_path:
            ensure_parent_dir(storage_path)

        self._engine: AsyncEngine = create_async_engine(config.sqlite_url, future=True, echo=config.echo)
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )

    async def ensure_collection(self, collection_name: str, vector_size: int) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(RagBase.metadata.create_all)

        async with self._session_factory() as session:
            record = await session.get(RagCollection, collection_name)
            if record is None:
                session.add(RagCollection(name=collection_name, vector_size=vector_size))
                await session.commit()
                return
            if record.vector_size != vector_size:
                raise ValueError(
                    f"rag collection '{collection_name}' has vector size {record.vector_size}, expected {vector_size}"
                )

    async def create_payload_index(self, collection_name: str, field_name: str, field_schema: str = "keyword") -> None:
        return None  # Indexes are declared on the model; nothing to build at runtime.

    async def upsert_points(self, collection_name: str, points: list[dict[str, Any]]) -> None:
        if not points:
            return
        rows = [
            RagChunk(
                id=str(point["id"]),
                collection=collection_name,
                vector=_pack_vector(point["vector"]),
                payload=point["payload"],
                **{key: _as_text(point["payload"].get(key)) for key in _SCALAR_KEYS},
            )
            for point in points
        ]
        async with self._session_factory() as session:
            await session.execute(
                delete(RagChunk).where(RagChunk.collection == collection_name, RagChunk.id.in_([r.id for r in rows]))
            )
            session.add_all(rows)
            await session.commit()

    async def delete_by_filter(self, collection_name: str, filters: dict[str, Any]) -> None:
        conditions, payload_any = _split_filters(filters)
        async with self._session_factory() as session:
            stmt = select(RagChunk.id, RagChunk.payload).where(RagChunk.collection == collection_name, *conditions)
            doomed = [
                row.id for row in (await session.execute(stmt)).all() if _payload_matches(row.payload, payload_any)
            ]
            if not doomed:
                return
            await session.execute(delete(RagChunk).where(RagChunk.id.in_(doomed)))
            await session.commit()

    async def search(
        self,
        collection_name: str,
        vector: list[float],
        *,
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        rows = await self._matching_rows(collection_name, filters, with_vector=True)
        if not rows:
            return []

        ranked = await asyncio.to_thread(_top_k, vector, [row.vector for row in rows], limit)
        return [{"id": rows[index].id, "score": score, "payload": rows[index].payload} for index, score in ranked]

    async def facet(
        self,
        collection_name: str,
        *,
        key: str,
        limit: int,
        filters: dict[str, Any] | None = None,
        exact: bool = False,
    ) -> list[dict[str, Any]]:
        rows = await self._matching_rows(collection_name, filters, with_vector=False)
        counter: Counter[str] = Counter()
        for row in rows:
            counter.update(_as_values(row.payload.get(key)))
        return [{"value": value, "count": count} for value, count in counter.most_common(limit)]

    async def _matching_rows(
        self, collection_name: str, filters: dict[str, Any] | None, *, with_vector: bool
    ) -> list[Any]:
        conditions, payload_any = _split_filters(filters)
        columns = (RagChunk.id, RagChunk.payload, RagChunk.vector) if with_vector else (RagChunk.id, RagChunk.payload)
        async with self._session_factory() as session:
            stmt = select(*columns).where(RagChunk.collection == collection_name, *conditions)
            rows = (await session.execute(stmt)).all()
        return [row for row in rows if _payload_matches(row.payload, payload_any)]


def _split_filters(filters: dict[str, Any] | None) -> tuple[list[Any], list[tuple[str, set[str]]]]:
    """Translate the ``VectorStore`` filter dict into SQL conditions plus payload list-membership checks.

    ``match.value`` keys are the indexed scalar columns and become SQL. ``match.any`` keys are
    list-valued payload fields (``tags``, ``categories``) and are checked in Python over the rows the
    SQL step already narrowed down.
    """
    if not filters:
        return [], []

    conditions: list[Any] = []
    payload_any: list[tuple[str, set[str]]] = []
    for condition in filters.get("must", []):
        key = condition["key"]
        match = condition.get("match", {})
        if "value" in match:
            column = getattr(RagChunk, key, None) if key in _SCALAR_KEYS else None
            if column is None:
                raise ValueError(f"unsupported rag filter key for exact match: {key!r}")
            conditions.append(column == match["value"])
        elif "any" in match:
            payload_any.append((key, set(match["any"])))
        else:
            raise ValueError(f"unsupported rag filter condition for key {key!r}")
    return conditions, payload_any


def _payload_matches(payload: dict[str, Any], payload_any: list[tuple[str, set[str]]]) -> bool:
    # List-valued filters run in Python over the SQL-narrowed rows, so tags/categories need no JSON
    # columns or json_each. Push them down with `json_each` if a filtered corpus grows enough to feel it.
    return all(allowed.intersection(_as_values(payload.get(key))) for key, allowed in payload_any)


def _as_values(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return [str(raw)]


def _as_text(raw: Any) -> str | None:
    return None if raw is None else str(raw)


def _pack_vector(vector: list[float]) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _top_k(query: list[float], vectors: list[bytes], limit: int) -> list[tuple[int, float]]:
    matrix = np.frombuffer(b"".join(vectors), dtype=np.float32).reshape(len(vectors), -1)
    scores = matrix @ np.asarray(query, dtype=np.float32)
    if limit >= len(scores):
        order = np.argsort(-scores)
    else:
        top = np.argpartition(-scores, limit)[:limit]
        order = top[np.argsort(-scores[top])]
    return [(int(index), float(scores[index])) for index in order]
