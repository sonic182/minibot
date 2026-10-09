from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from minibot.adapters.graph.sqlite import SqliteGraphStore
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.graph import build_graph_tools


@pytest_asyncio.fixture
async def graph_store(tmp_path: Path):
    store = SqliteGraphStore(f"sqlite+aiosqlite:///{tmp_path}/graph.db")
    yield store
    await store.close()


def _bindings(store: SqliteGraphStore) -> dict[str, ToolBinding]:
    return {binding.tool.name: binding for binding in build_graph_tools(store)}


async def _call(bindings: dict[str, ToolBinding], name: str, payload: dict) -> dict:
    tool = bindings[name].tool
    full_payload = {field: None for field in tool.parameters["required"]} | payload
    return await bindings[name].handler(full_payload, ToolContext(owner_id="owner-a"))


def test_graph_is_split_into_one_schema_per_operation(graph_store: SqliteGraphStore) -> None:
    tools = {name: binding.tool for name, binding in _bindings(graph_store).items()}

    assert set(tools) == {
        "graph_link",
        "graph_unlink",
        "graph_merge",
        "graph_neighbors",
        "graph_path",
        "graph_search",
    }
    assert all("action" not in tool.parameters["properties"] for tool in tools.values())
    assert all(tool.description for tool in tools.values())


@pytest.mark.asyncio
async def test_graph_tools_link_search_and_unlink(graph_store: SqliteGraphStore) -> None:
    bindings = _bindings(graph_store)
    edge = {"source": "person:alex", "rel": "uses", "target": "tech:vim"}

    await _call(bindings, "graph_link", edge)
    live = await _call(bindings, "graph_search", {"query": "vim"})
    await _call(bindings, "graph_unlink", edge)
    after = await _call(bindings, "graph_search", {"query": "vim"})
    history = await _call(bindings, "graph_search", {"query": "vim", "history": True})

    assert [(e["source"], e["rel"], e["target"]) for e in live["edges"]] == [("person:alex", "uses", "tech:vim")]
    assert after["edges"] == []
    assert history["edges"][0]["valid_to"] is not None
