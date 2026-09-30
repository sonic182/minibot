from __future__ import annotations

from dataclasses import dataclass

from llm_async.models import Tool

from minibot.core.tools import ToolContext, ToolHandler, ToolPayload

__all__ = ["ToolBinding", "ToolContext", "ToolHandler", "ToolPayload"]


@dataclass(frozen=True)
class ToolBinding:
    tool: Tool
    handler: ToolHandler
