from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from llm_async.models import Tool

from minibot.core.agent_runtime import ToolResult

ToolPayload = dict[str, Any]


@dataclass(frozen=True)
class ToolContext:
    owner_id: str | None = None
    channel: str | None = None
    chat_id: int | None = None
    user_id: int | None = None
    session_id: str | None = None
    message_id: int | None = None
    agent_name: str | None = None
    can_enqueue_agent_jobs: bool = True


ToolHandler = Callable[[ToolPayload, ToolContext], Awaitable[ToolResult | Any]]


@dataclass(frozen=True)
class ToolBinding:
    tool: Tool
    handler: ToolHandler
