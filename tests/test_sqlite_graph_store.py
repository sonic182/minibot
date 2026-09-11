from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from minibot.adapters.graph.sqlite import SqliteGraphStore
from minibot.extensions.tools.graph import _GraphStoreService


@pytest_asyncio.fixture
async def graph_store(tmp_path: Path):
    store = SqliteGraphStore(f"sqlite+aiosqlite:///{tmp_path}/graph.db")
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_graph_store_normalizes_ids_and_isolates_owner_and_namespace(graph_store: SqliteGraphStore) -> None:
    created = await graph_store.link(
        graph="memory",
        owner_id="owner-a",
        source="Person:José Álvarez",
        rel="Works-On",
        target="Project:Mini Bot",
    )

    assert created["edge"]["source"] == "person:jose_alvarez"
    assert created["edge"]["rel"] == "works_on"
    assert created["edge"]["target"] == "project:mini_bot"

    found = await graph_store.neighbors(
        graph="memory",
        owner_id="owner-a",
        node="PERSON:JOSÉ-ÁLVAREZ",
    )
    other_owner = await graph_store.neighbors(
        graph="memory",
        owner_id="owner-b",
        node="person:jose_alvarez",
    )
    other_namespace = await graph_store.neighbors(
        graph="private",
        owner_id="owner-a",
        node="person:jose_alvarez",
    )

    assert found["nodes"] == ["person:jose_alvarez", "project:mini_bot"]
    assert other_owner["found"] is False
    assert other_namespace["found"] is False


@pytest.mark.asyncio
async def test_neighbors_returns_distance_ordered_bounded_subgraph(graph_store: SqliteGraphStore) -> None:
    for target in ("node:beta", "node:alpha"):
        await graph_store.link(graph="memory", owner_id="owner", source="node:root", rel="connects", target=target)
    await graph_store.link(graph="memory", owner_id="owner", source="node:alpha", rel="connects", target="node:child")

    result = await graph_store.neighbors(
        graph="memory",
        owner_id="owner",
        node="node:root",
        direction="out",
        depth=2,
        max_nodes=2,
    )

    assert result["nodes"] == ["node:root", "node:alpha"]
    assert result["truncated"] is True
    assert [(edge["source"], edge["rel"], edge["target"]) for edge in result["edges"]] == [
        ("node:root", "connects", "node:alpha")
    ]


@pytest.mark.asyncio
async def test_search_treats_normalized_underscores_as_literals(graph_store: SqliteGraphStore) -> None:
    await graph_store.link(
        graph="memory", owner_id="owner", source="device:work-laptop", rel="runs", target="os:linux"
    )
    await graph_store.link(
        graph="memory", owner_id="owner", source="device:workxlaptop", rel="runs", target="os:other"
    )

    result = await graph_store.search(graph="memory", owner_id="owner", query="work laptop")

    assert [edge["source"] for edge in result["edges"]] == ["device:work_laptop"]


@pytest.mark.asyncio
async def test_merge_keeps_canonical_live_edge_and_rekeys_history(graph_store: SqliteGraphStore) -> None:
    await graph_store.link(
        graph="memory",
        owner_id="owner",
        source="person:user",
        rel="prefers",
        target="tech:neovim",
        attrs={"source": "duplicate"},
    )
    await graph_store.link(
        graph="memory",
        owner_id="owner",
        source="person:johanderson",
        rel="prefers",
        target="tech:neovim",
        attrs={"source": "canonical"},
    )
    await graph_store.link(graph="memory", owner_id="owner", source="person:user", rel="uses", target="tech:vim")
    await graph_store.unlink(graph="memory", owner_id="owner", source="person:user", rel="uses", target="tech:vim")

    merged = await graph_store.merge(
        graph="memory", owner_id="owner", source="person:user", target="person:johanderson"
    )
    live = await graph_store.search(graph="memory", owner_id="owner", query="neovim")
    history = await graph_store.search(graph="memory", owner_id="owner", query="vim", history=True)

    assert merged["active_duplicates_discarded"] == 1
    assert merged["history_edges_rekeyed"] == 1
    assert live["edges"] == [
        {
            "source": "person:johanderson",
            "rel": "prefers",
            "target": "tech:neovim",
            "attrs": {"source": "canonical"},
            "valid_from": live["edges"][0]["valid_from"],
        }
    ]
    closed = next(edge for edge in history["edges"] if edge.get("valid_to"))
    assert closed["source"] == "person:johanderson"


@pytest.mark.asyncio
async def test_merge_rekeys_self_loop_once(graph_store: SqliteGraphStore) -> None:
    await graph_store.link(graph="memory", owner_id="owner", source="person:user", rel="knows", target="person:user")

    merged = await graph_store.merge(
        graph="memory", owner_id="owner", source="person:user", target="person:johanderson"
    )
    result = await graph_store.search(graph="memory", owner_id="owner", query="johanderson")

    assert merged["edges_rewritten"] == 1
    assert result["edges"][0]["source"] == "person:johanderson"
    assert result["edges"][0]["target"] == "person:johanderson"


@pytest.mark.asyncio
async def test_graph_store_service_closes_store() -> None:
    class _StoreProbe:
        closed = False

        async def close(self) -> None:
            self.closed = True

    store = _StoreProbe()
    service = _GraphStoreService(store)  # type: ignore[arg-type]

    await service.start()
    await service.stop()

    assert store.closed is True
