from __future__ import annotations

from dataclasses import dataclass

from llm_async.models import Tool

from minibot.core.tools import ToolContext, ToolHandler, ToolPayload

__all__ = ["ToolBinding", "ToolContext", "ToolHandler", "ToolPayload"]


@dataclass(frozen=True)
class ToolBinding:
    """A tool schema and its handler.

    ``spill_after_chars`` lets a tool keep larger results inline than ``[tools.tool_output_spill]``
    ``spill_after_chars`` (it never lowers it). Where spill cannot run, the tool's results are cut to it.
    """

    tool: Tool
    handler: ToolHandler
    spill_after_chars: int | None = None
