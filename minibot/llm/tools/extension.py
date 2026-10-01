from __future__ import annotations

from typing import Any

from llm_async.models import Tool

from minibot.core.tools import ToolHandler
from minibot.llm.tools.base import ToolBinding


def build_extension_tool_binding(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    handler: ToolHandler,
) -> ToolBinding:
    return ToolBinding(
        tool=Tool(name=name, description=description, parameters=parameters),
        handler=handler,
    )
