"""Relation-graph tool backed by :class:`SqliteGraphStore`."""

from __future__ import annotations

import json
from typing import Any

from llm_async.models import Tool

from minibot.adapters.graph.sqlite import SqliteGraphStore
from minibot.llm.tools.action_dispatcher import dispatch_action
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

GRAPH_ACTIONS = ("link", "unlink", "neighbors", "path", "search")
DIRECTIONS = ("out", "in", "both")
DEFAULT_NAMESPACE = "memory"


def build_graph_tools(store: SqliteGraphStore) -> list[ToolBinding]:
    return [ToolBinding(tool=_graph_tool(), handler=lambda payload, ctx: _graph_action(store, payload, ctx))]


def _graph_tool() -> Tool:
    return Tool(
        name="graph",
        description=load_tool_description("graph"),
        parameters=strict_object(
            properties={
                "action": {
                    "type": "string",
                    "enum": list(GRAPH_ACTIONS),
                    "description": "Graph operation to perform.",
                },
                "graph": nullable_string(
                    f'Graph namespace. Defaults to "{DEFAULT_NAMESPACE}". Use a separate namespace '
                    "only for a clearly different domain, never to shard the same one."
                ),
                "source": nullable_string('Edge origin for link and unlink, e.g. "person:johanderson".'),
                "rel": nullable_string(
                    'Relation type for link and unlink, e.g. "prefers". Optional filter for neighbors.'
                ),
                "target": nullable_string('Edge destination for link and unlink, e.g. "tech:vue".'),
                "attrs": nullable_string("Optional JSON object with extra edge fields, for link only."),
                "node": nullable_string("Entry-point node for neighbors."),
                "direction": _nullable_direction_schema(),
                "depth": nullable_integer(minimum=1, description="Hops to expand for neighbors. Defaults to 1."),
                "max_depth": nullable_integer(minimum=1, description="Longest path accepted. Defaults to 4."),
                "history": nullable_boolean("When true, also return closed edges. Defaults to false."),
                "query": nullable_string("Text fragment matched against source, rel, and target for search."),
                "limit": nullable_integer(minimum=1, description="Maximum edges returned."),
            },
            required=[
                "action",
                "graph",
                "source",
                "rel",
                "target",
                "attrs",
                "node",
                "direction",
                "depth",
                "max_depth",
                "history",
                "query",
                "limit",
            ],
        ),
    )


def _nullable_direction_schema() -> dict[str, Any]:
    return {
        "anyOf": [{"type": "string", "enum": list(DIRECTIONS)}, {"type": "null"}],
        "description": 'Traversal direction for neighbors. "in" walks edges backwards. Defaults to "out".',
    }


async def _graph_action(store: SqliteGraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    action = (optional_str(payload.get("action")) or "").lower()
    handlers = {
        "link": lambda pl, ctx: _link(store, pl, ctx),
        "unlink": lambda pl, ctx: _unlink(store, pl, ctx),
        "neighbors": lambda pl, ctx: _neighbors(store, pl, ctx),
        "path": lambda pl, ctx: _path(store, pl, ctx),
        "search": lambda pl, ctx: _search(store, pl, ctx),
    }
    return await dispatch_action(
        action=action,
        payload=payload,
        context=context,
        handlers=handlers,
        error_message=f"action must be one of: {', '.join(GRAPH_ACTIONS)}",
    )


async def _link(store: SqliteGraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    return await store.link(
        graph=_namespace(payload),
        owner_id=require_owner(context),
        source=require_non_empty_str(payload, "source"),
        rel=require_non_empty_str(payload, "rel"),
        target=require_non_empty_str(payload, "target"),
        attrs=_coerce_attrs(payload.get("attrs")),
    )


async def _unlink(store: SqliteGraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    return await store.unlink(
        graph=_namespace(payload),
        owner_id=require_owner(context),
        source=require_non_empty_str(payload, "source"),
        rel=require_non_empty_str(payload, "rel"),
        target=require_non_empty_str(payload, "target"),
    )


async def _neighbors(store: SqliteGraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
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
    )


async def _path(store: SqliteGraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    return await store.path(
        graph=_namespace(payload),
        owner_id=require_owner(context),
        source=require_non_empty_str(payload, "source"),
        target=require_non_empty_str(payload, "target"),
        max_depth=int_with_default(
            payload.get("max_depth"), default=4, field="max_depth", min_value=1, max_value=8, clamp_max=True
        ),
    )


async def _search(store: SqliteGraphStore, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
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
