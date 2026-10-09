"""Relation-graph tools backed by :class:`SqliteGraphStore`, one tool per operation."""

from __future__ import annotations

import json
from typing import Any

from llm_async.models import Tool

from minibot.core.graph import GraphStore
from minibot.llm.tools.arg_utils import (
    int_with_default,
    optional_bool,
    optional_str,
    require_non_empty_str,
    require_owner,
)
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_boolean, nullable_integer, nullable_string, strict_object

DIRECTIONS = ("out", "in", "both")
DEFAULT_NAMESPACE = "memory"


def build_graph_tools(store: GraphStore) -> list[ToolBinding]:
    return [
        ToolBinding(tool=_link_tool(), handler=lambda payload, ctx: _link(store, payload, ctx)),
        ToolBinding(tool=_unlink_tool(), handler=lambda payload, ctx: _unlink(store, payload, ctx)),
        ToolBinding(tool=_merge_tool(), handler=lambda payload, ctx: _merge(store, payload, ctx)),
        ToolBinding(tool=_neighbors_tool(), handler=lambda payload, ctx: _neighbors(store, payload, ctx)),
        ToolBinding(tool=_path_tool(), handler=lambda payload, ctx: _path(store, payload, ctx)),
        ToolBinding(tool=_search_tool(), handler=lambda payload, ctx: _search(store, payload, ctx)),
    ]


def _graph_tool(name: str, properties: dict[str, Any]) -> Tool:
    properties = {"graph": _namespace_schema(), **properties}
    return Tool(
        name=name,
        description=load_tool_description(name),
        parameters=strict_object(properties=properties, required=list(properties)),
    )


def _link_tool() -> Tool:
    return _graph_tool(
        "graph_link",
        {
            "source": nullable_string('Edge origin, e.g. "person:johanderson".'),
            "rel": nullable_string('Relation type, e.g. "prefers".'),
            "target": nullable_string('Edge destination, e.g. "tech:vue".'),
            "attrs": nullable_string("Optional JSON object with extra edge fields."),
        },
    )


def _unlink_tool() -> Tool:
    return _graph_tool(
        "graph_unlink",
        {
            "source": nullable_string("Edge origin."),
            "rel": nullable_string("Relation type."),
            "target": nullable_string("Edge destination."),
        },
    )


def _merge_tool() -> Tool:
    return _graph_tool(
        "graph_merge",
        {
            "source": nullable_string("The wrong, duplicate node id."),
            "target": nullable_string("The canonical node id to keep."),
        },
    )


def _neighbors_tool() -> Tool:
    return _graph_tool(
        "graph_neighbors",
        {
            "node": nullable_string("Entry-point node."),
            "direction": _nullable_direction_schema(),
            "depth": nullable_integer(minimum=1, description="Hops to expand. Defaults to 1."),
            "rel": nullable_string("Only follow this relation."),
            "history": _history_schema(),
            "limit": nullable_integer(minimum=1, description="Maximum edges returned. Defaults to 50."),
            "max_nodes": nullable_integer(minimum=1, description="Maximum nodes returned. Defaults to 100."),
        },
    )


def _path_tool() -> Tool:
    return _graph_tool(
        "graph_path",
        {
            "source": nullable_string("Node to start from."),
            "target": nullable_string("Node to reach."),
            "max_depth": nullable_integer(minimum=1, description="Longest path accepted. Defaults to 4."),
        },
    )


def _search_tool() -> Tool:
    return _graph_tool(
        "graph_search",
        {
            "query": nullable_string("Text fragment matched against source, rel, and target."),
            "limit": nullable_integer(minimum=1, description="Maximum edges returned. Defaults to 25."),
            "history": _history_schema(),
        },
    )


def _namespace_schema() -> dict[str, Any]:
    return nullable_string(
        f'Graph namespace. Defaults to "{DEFAULT_NAMESPACE}". Use a separate namespace '
        "only for a clearly different domain, never to shard the same one."
    )


def _history_schema() -> dict[str, Any]:
    return nullable_boolean("When true, also return closed edges. Defaults to false.")


def _nullable_direction_schema() -> dict[str, Any]:
    return {
        "anyOf": [{"type": "string", "enum": list(DIRECTIONS)}, {"type": "null"}],
        "description": '"in" walks edges backwards to find what points at the node. Defaults to "out".',
    }


async def _link(store: GraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    return await store.link(
        graph=_namespace(payload),
        owner_id=require_owner(context),
        source=require_non_empty_str(payload, "source"),
        rel=require_non_empty_str(payload, "rel"),
        target=require_non_empty_str(payload, "target"),
        attrs=_coerce_attrs(payload.get("attrs")),
    )


async def _unlink(store: GraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    return await store.unlink(
        graph=_namespace(payload),
        owner_id=require_owner(context),
        source=require_non_empty_str(payload, "source"),
        rel=require_non_empty_str(payload, "rel"),
        target=require_non_empty_str(payload, "target"),
    )


async def _merge(store: GraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    return await store.merge(
        graph=_namespace(payload),
        owner_id=require_owner(context),
        source=require_non_empty_str(payload, "source"),
        target=require_non_empty_str(payload, "target"),
    )


async def _neighbors(store: GraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    return await store.neighbors(
        graph=_namespace(payload),
        owner_id=require_owner(context),
        node=require_non_empty_str(payload, "node"),
        direction=_direction(payload),
        depth=int_with_default(
            payload.get("depth"), default=1, field="depth", min_value=1, max_value=5, clamp_max=True
        ),
        rel=optional_str(payload.get("rel")),
        history=optional_bool(payload.get("history"), default=False, error_message="history must be a boolean"),
        limit=_limit(payload, default=50),
        max_nodes=int_with_default(
            payload.get("max_nodes"), default=100, field="max_nodes", min_value=1, max_value=200, clamp_max=True
        ),
    )


async def _path(store: GraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    return await store.path(
        graph=_namespace(payload),
        owner_id=require_owner(context),
        source=require_non_empty_str(payload, "source"),
        target=require_non_empty_str(payload, "target"),
        max_depth=int_with_default(
            payload.get("max_depth"), default=4, field="max_depth", min_value=1, max_value=8, clamp_max=True
        ),
    )


async def _search(store: GraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    return await store.search(
        graph=_namespace(payload),
        owner_id=require_owner(context),
        query=require_non_empty_str(payload, "query"),
        limit=_limit(payload, default=25),
        history=optional_bool(payload.get("history"), default=False, error_message="history must be a boolean"),
    )


def _namespace(payload: dict[str, Any]) -> str:
    return optional_str(payload.get("graph")) or DEFAULT_NAMESPACE


def _direction(payload: dict[str, Any]) -> str:
    direction = (optional_str(payload.get("direction")) or "out").lower()
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of: {', '.join(DIRECTIONS)}")
    return direction


def _limit(payload: dict[str, Any], *, default: int) -> int:
    return int_with_default(
        payload.get("limit"), default=default, field="limit", min_value=1, max_value=200, clamp_max=True
    )


def _coerce_attrs(value: Any) -> dict[str, Any] | None:
    raw = optional_str(value, error_message="attrs must be a JSON object string")
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("attrs must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("attrs must be a JSON object")
    return parsed
