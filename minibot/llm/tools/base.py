from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from llm_async.models import Tool

ToolPayload = dict[str, Any]


@dataclass(frozen=True)
class ToolContext:
    owner_id: str | None = None


ToolHandler = Callable[[ToolPayload, ToolContext], Awaitable[Any]]


@dataclass(frozen=True)
class ToolBinding:
    tool: Tool
    handler: ToolHandler
