from __future__ import annotations

import asyncio
from typing import Any

from llm_async.models import Tool

from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import integer_field, strict_object


class WaitTool:
    def __init__(self, max_milliseconds: int = 30000) -> None:
        self._max_milliseconds = max_milliseconds

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        return Tool(
            name="wait",
            description=load_tool_description("wait"),
            parameters=strict_object(
                properties={
                    "milliseconds": integer_field(minimum=1, description="Duration to sleep in milliseconds."),
                },
                required=["milliseconds"],
            ),
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        requested = payload.get("milliseconds")
        if not isinstance(requested, int) or isinstance(requested, bool):
            return {"ok": False, "error": "milliseconds must be a positive integer"}
        if requested < 1:
            return {"ok": False, "error": "milliseconds must be >= 1"}
        clamped = min(requested, self._max_milliseconds)
        await asyncio.sleep(clamped / 1000)
        return {"ok": True, "slept_ms": clamped, "requested_ms": requested}
