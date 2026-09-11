"""SQLite-backed relation graph.

SQLite is authoritative: task workers are forked processes (``adapters/tasks/manager.py``), so the
store has to survive concurrent writers, which is what WAL mode is for. NetworkX is the algorithm
engine only, materialized per query and never handed out past this module — a future Cypher backend
implements the same six methods without the tool layer noticing.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from datetime import datetime
from typing import Any

import networkx as nx
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Table,
    Text,
    case,
    delete,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url
from sqlalchemy.event import listens_for
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateIndex, CreateTable

from minibot.adapters.sqlalchemy_utils import ensure_parent_dir, resolve_sqlite_storage_path
from minibot.shared.datetime_utils import ensure_utc, utcnow

DEFAULT_SQLITE_URL = "sqlite+aiosqlite:///./data/graph.db"

_METADATA = MetaData()

GRAPH_EDGES = Table(
    "graph_edges",
    _METADATA,
    Column("graph", String(64), nullable=False),
    Column("owner_id", String(128), nullable=False),
    Column("source", String(256), nullable=False),
    Column("rel", String(128), nullable=False),
    Column("target", String(256), nullable=False),
    Column("attrs", Text, nullable=True),
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("valid_to", DateTime(timezone=True), nullable=True),
    Index("ix_graph_edges_source", "graph", "owner_id", "source"),
    Index("ix_graph_edges_target", "graph", "owner_id", "target"),
    # One live edge per (namespace, owner, source, rel, target). Without this partial index two
    # identical link calls a millisecond apart would both insert.
    Index(
        "ux_graph_edges_active",
        "graph",
        "owner_id",
        "source",
        "rel",
        "target",
        unique=True,
        sqlite_where=text("valid_to IS NULL"),
    ),
)

_CONFLICT_KEYS = ["graph", "owner_id", "source", "rel", "target"]
_LIVE = text("valid_to IS NULL")


class SqliteGraphStore:
    """Typed relations between entities, with history via ``valid_to``."""

    def __init__(self, sqlite_url: str = DEFAULT_SQLITE_URL, *, echo: bool = False) -> None:
        storage_path = resolve_sqlite_storage_path(sqlite_url)
        if storage_path:
            ensure_parent_dir(storage_path)
        self._engine: AsyncEngine = create_async_engine(sqlite_url, future=True, echo=echo)
        if make_url(sqlite_url).drivername.startswith("sqlite"):
            _apply_sqlite_pragmas(self._engine)
        self._session_factory = async_sessionmaker(bind=self._engine, expire_on_commit=False)
        self._ready = False
        self._ready_lock = asyncio.Lock()

    async def link(
        self,
        *,
        graph: str,
        owner_id: str,
        source: str,
        rel: str,
        target: str,
        attrs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self._ensure_schema()
        source, rel, target = _normalize_node(source), _normalize_rel(rel), _normalize_node(target)
        now = utcnow()
        stmt = sqlite_insert(GRAPH_EDGES).values(
            graph=graph,
            owner_id=owner_id,
            source=source,
            rel=rel,
            target=target,
            attrs=json.dumps(attrs) if attrs else None,
            valid_from=now,
            valid_to=None,
        )
        upsert = stmt.on_conflict_do_update(
            index_elements=_CONFLICT_KEYS,
            index_where=_LIVE,
            set_={"attrs": stmt.excluded.attrs},
        ).returning(GRAPH_EDGES.c.valid_from)
        async with self._session_factory() as session:
            stored = (await session.execute(upsert)).scalar_one()
            await session.commit()
        # The stored valid_from only matches the one just generated when this call inserted.
        return {
            "created": ensure_utc(stored) == now,
            "edge": _edge_payload(source=source, rel=rel, target=target, attrs=attrs, valid_from=stored),
        }

    async def unlink(self, *, graph: str, owner_id: str, source: str, rel: str, target: str) -> dict[str, Any]:
        await self._ensure_schema()
        source, rel, target = _normalize_node(source), _normalize_rel(rel), _normalize_node(target)
        stmt = (
            update(GRAPH_EDGES)
            .where(
                GRAPH_EDGES.c.graph == graph,
                GRAPH_EDGES.c.owner_id == owner_id,
                GRAPH_EDGES.c.source == source,
                GRAPH_EDGES.c.rel == rel,
                GRAPH_EDGES.c.target == target,
                GRAPH_EDGES.c.valid_to.is_(None),
            )
            .values(valid_to=utcnow())
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            await session.commit()
        return {"closed": bool(result.rowcount)}

    async def neighbors(
        self,
        *,
        graph: str,
        owner_id: str,
        node: str,
        direction: str = "out",
        depth: int = 1,
        rel: str | None = None,
        history: bool = False,
        limit: int = 100,
        max_nodes: int = 100,
    ) -> dict[str, Any]:
        await self._ensure_schema()
        node = _normalize_node(node)
        edges = await self._live_edges(graph=graph, owner_id=owner_id, rel=_normalize_rel(rel) if rel else None)
        materialized = _materialize(edges)
        if node not in materialized:
            return {"node": node, "found": False, "nodes": [], "edges": [], "history": [], "truncated": False}

        distances = _distances(materialized, node=node, direction=direction, depth=depth)
        nodes = sorted(distances, key=lambda reached: (distances[reached], reached))[:max_nodes]
        reached = set(nodes)
        selected = [edge for edge in edges if edge["source"] in reached and edge["target"] in reached][:limit]
        past = await self._closed_edges(graph=graph, owner_id=owner_id, nodes=reached, limit=limit) if history else []
        return {
            "node": node,
            "found": True,
            "nodes": nodes,
            "edges": [_edge_payload(**edge) for edge in selected],
            "history": past,
            "truncated": len(distances) > len(nodes),
        }

    async def path(self, *, graph: str, owner_id: str, source: str, target: str, max_depth: int = 4) -> dict[str, Any]:
        await self._ensure_schema()
        source, target = _normalize_node(source), _normalize_node(target)
        edges = await self._live_edges(graph=graph, owner_id=owner_id)
        materialized = _materialize(edges)
        if source not in materialized or target not in materialized:
            missing = [n for n in (source, target) if n not in materialized]
            return {"found": False, "reason": f"unknown node(s): {', '.join(missing)}", "nodes": [], "hops": []}
        try:
            # Undirected: "how are X and Y related" has to traverse an edge backwards, which is how
            # person -prefers-> vue <-migration_target- project resolves at all.
            nodes = nx.shortest_path(materialized.to_undirected(as_view=True), source, target)
        except nx.NetworkXNoPath:
            return {"found": False, "reason": "no path", "nodes": [], "hops": []}
        if len(nodes) - 1 > max_depth:
            return {
                "found": False,
                "reason": f"shortest path is longer than max_depth={max_depth}",
                "nodes": [],
                "hops": [],
            }
        return {"found": True, "nodes": nodes, "hops": _hops(materialized, nodes)}

    async def search(
        self, *, graph: str, owner_id: str, query: str, limit: int = 25, history: bool = False
    ) -> dict[str, Any]:
        await self._ensure_schema()
        pattern = _like_pattern(_slug(query, field="query"))
        stmt = select(GRAPH_EDGES).where(
            GRAPH_EDGES.c.graph == graph,
            GRAPH_EDGES.c.owner_id == owner_id,
            or_(
                GRAPH_EDGES.c.source.like(pattern, escape="\\"),
                GRAPH_EDGES.c.rel.like(pattern, escape="\\"),
                GRAPH_EDGES.c.target.like(pattern, escape="\\"),
            ),
        )
        if not history:
            stmt = stmt.where(GRAPH_EDGES.c.valid_to.is_(None))
        stmt = stmt.order_by(GRAPH_EDGES.c.valid_from.desc()).limit(limit)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).mappings().all()
        return {"query": query, "edges": [_edge_payload(**_row_to_edge(row)) for row in rows]}

    async def merge(self, *, graph: str, owner_id: str, source: str, target: str) -> dict[str, Any]:
        """Rewrite every edge mentioning ``source`` to ``target``.

        The target id is canonical. If both nodes have the same live relation, the target edge
        keeps its attrs and dates while the source duplicate is discarded. Closed edges are only
        re-keyed: separate historical intervals remain meaningful even if their triples match.
        """
        await self._ensure_schema()
        source, target = _normalize_node(source), _normalize_node(target)
        if source == target:
            raise ValueError("source and target are the same node")
        active_rekeyed = 0
        active_duplicates_discarded = 0
        async with self._session_factory() as session:
            # A merge makes multiple read/write decisions; serialize it with other SQLite writers.
            await session.execute(text("BEGIN IMMEDIATE"))
            live_rows = (
                await session.execute(
                    select(GRAPH_EDGES).where(
                        GRAPH_EDGES.c.graph == graph,
                        GRAPH_EDGES.c.owner_id == owner_id,
                        GRAPH_EDGES.c.valid_to.is_(None),
                    )
                )
            ).mappings()
            active_edges = [dict(row) for row in live_rows]
            canonical_keys = {
                _edge_key(edge) for edge in active_edges if edge["source"] != source and edge["target"] != source
            }
            affected = sorted(
                (edge for edge in active_edges if edge["source"] == source or edge["target"] == source),
                key=_merge_sort_key,
            )
            for edge in affected:
                merged_source = target if edge["source"] == source else edge["source"]
                merged_target = target if edge["target"] == source else edge["target"]
                merged_key = (merged_source, edge["rel"], merged_target)
                row_filter = _live_edge_filter(graph=graph, owner_id=owner_id, edge=edge)
                if merged_key in canonical_keys:
                    result = await session.execute(delete(GRAPH_EDGES).where(*row_filter))
                    active_duplicates_discarded += result.rowcount
                    continue
                result = await session.execute(
                    update(GRAPH_EDGES).where(*row_filter).values(source=merged_source, target=merged_target)
                )
                active_rekeyed += result.rowcount
                canonical_keys.add(merged_key)
            history_result = await session.execute(
                update(GRAPH_EDGES)
                .where(
                    GRAPH_EDGES.c.graph == graph,
                    GRAPH_EDGES.c.owner_id == owner_id,
                    GRAPH_EDGES.c.valid_to.is_not(None),
                    or_(GRAPH_EDGES.c.source == source, GRAPH_EDGES.c.target == source),
                )
                .values(
                    source=case((GRAPH_EDGES.c.source == source, target), else_=GRAPH_EDGES.c.source),
                    target=case((GRAPH_EDGES.c.target == source, target), else_=GRAPH_EDGES.c.target),
                )
            )
            await session.commit()
        history_rekeyed = history_result.rowcount
        return {
            "merged": source,
            "into": target,
            "edges_rewritten": active_rekeyed + history_rekeyed,
            "active_duplicates_discarded": active_duplicates_discarded,
            "history_edges_rekeyed": history_rekeyed,
        }

    async def close(self) -> None:
        await self._engine.dispose()

    async def _ensure_schema(self) -> None:
        if self._ready:
            return
        async with self._ready_lock:
            if self._ready:
                return
            # Native IF NOT EXISTS rather than create_all: its checkfirst does a SELECT and then a
            # CREATE, and on a cold start the daemon and a forked worker can interleave those two
            # and crash one of them.
            async with self._engine.begin() as conn:
                await conn.execute(CreateTable(GRAPH_EDGES, if_not_exists=True))
                for index in GRAPH_EDGES.indexes:
                    await conn.execute(CreateIndex(index, if_not_exists=True))
            self._ready = True

    async def _live_edges(self, *, graph: str, owner_id: str, rel: str | None = None) -> list[dict[str, Any]]:
        stmt = select(GRAPH_EDGES).where(
            GRAPH_EDGES.c.graph == graph,
            GRAPH_EDGES.c.owner_id == owner_id,
            GRAPH_EDGES.c.valid_to.is_(None),
        )
        if rel:
            stmt = stmt.where(GRAPH_EDGES.c.rel == rel)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).mappings().all()
        return [_row_to_edge(row) for row in rows]

    async def _closed_edges(self, *, graph: str, owner_id: str, nodes: set[str], limit: int) -> list[dict[str, Any]]:
        stmt = (
            select(GRAPH_EDGES)
            .where(
                GRAPH_EDGES.c.graph == graph,
                GRAPH_EDGES.c.owner_id == owner_id,
                GRAPH_EDGES.c.valid_to.is_not(None),
                or_(GRAPH_EDGES.c.source.in_(nodes), GRAPH_EDGES.c.target.in_(nodes)),
            )
            .order_by(GRAPH_EDGES.c.valid_to.desc())
            .limit(limit)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).mappings().all()
        return [_edge_payload(**_row_to_edge(row)) for row in rows]


def _slug(value: str, *, field: str) -> str:
    # Deterministic format normalization, not interpretation: "Arch-Linux" and "arch_linux" are the
    # same node, and a graph whose ids do not converge is only a slower key-value store.
    folded = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(character for character in folded if not unicodedata.combining(character))
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_only.lower()).strip("_")
    if not slug:
        raise ValueError(f"{field} must contain at least one letter or digit")
    return slug


def _normalize_node(value: str) -> str:
    prefix, separator, rest = value.partition(":")
    if not separator:
        return _slug(value, field="node id")
    return f"{_slug(prefix, field='node type')}:{_slug(rest, field='node id')}"


def _normalize_rel(value: str) -> str:
    return _slug(value, field="rel")


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str]:
    return edge["source"], edge["rel"], edge["target"]


def _merge_sort_key(edge: dict[str, Any]) -> tuple[datetime, str, str, str]:
    return ensure_utc(edge["valid_from"]), edge["source"], edge["rel"], edge["target"]


def _live_edge_filter(*, graph: str, owner_id: str, edge: dict[str, Any]) -> tuple[Any, ...]:
    return (
        GRAPH_EDGES.c.graph == graph,
        GRAPH_EDGES.c.owner_id == owner_id,
        GRAPH_EDGES.c.source == edge["source"],
        GRAPH_EDGES.c.rel == edge["rel"],
        GRAPH_EDGES.c.target == edge["target"],
        GRAPH_EDGES.c.valid_to.is_(None),
    )


def _apply_sqlite_pragmas(engine: AsyncEngine) -> None:
    @listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
        # WAL is what lets the forked task workers write this file alongside the daemon. It requires
        # a local filesystem; on NFS this degrades and concurrent writers are no longer safe.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def _materialize(edges: list[dict[str, Any]]) -> nx.MultiDiGraph:
    # Loads the whole namespace per call. Fine to ~10k edges; past that push neighbors/path into
    # recursive SQL and keep the full load only for whole-graph analysis.
    graph = nx.MultiDiGraph()
    for edge in edges:
        graph.add_edge(edge["source"], edge["target"], key=edge["rel"])
    return graph


def _distances(graph: nx.MultiDiGraph, *, node: str, direction: str, depth: int) -> dict[str, int]:
    if direction == "in":
        traversal = graph.reverse(copy=False)
    elif direction == "both":
        traversal = graph.to_undirected(as_view=True)
    else:
        traversal = graph
    return dict(nx.single_source_shortest_path_length(traversal, node, cutoff=depth))


def _hops(graph: nx.MultiDiGraph, nodes: list[str]) -> list[dict[str, str]]:
    hops: list[dict[str, str]] = []
    for left, right in zip(nodes, nodes[1:], strict=False):
        for rel in graph.succ[left].get(right, {}):
            hops.append({"source": left, "rel": rel, "target": right, "direction": "forward"})
        for rel in graph.succ[right].get(left, {}):
            hops.append({"source": right, "rel": rel, "target": left, "direction": "backward"})
    return hops


def _row_to_edge(row: Any) -> dict[str, Any]:
    return {
        "source": row["source"],
        "rel": row["rel"],
        "target": row["target"],
        "attrs": json.loads(row["attrs"]) if row["attrs"] else None,
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
    }


def _edge_payload(
    *,
    source: str,
    rel: str,
    target: str,
    attrs: dict[str, Any] | None = None,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"source": source, "rel": rel, "target": target}
    if attrs:
        payload["attrs"] = attrs
    if valid_from is not None:
        payload["valid_from"] = ensure_utc(valid_from).isoformat()
    if valid_to is not None:
        payload["valid_to"] = ensure_utc(valid_to).isoformat()
    return payload
