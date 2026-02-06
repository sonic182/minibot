from __future__ import annotations

from datetime import datetime, timezone

from llm_async.models import Tool

from minibot.llm.tools.base import ToolBinding, ToolContext


class CurrentTimeTool:
    def __init__(self, default_format: str = "%Y-%m-%dT%H:%M:%SZ") -> None:
        self._default_format = default_format

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        description = f"Return the current datetime in UTC (default format {self._default_format})."
        return Tool(
            name="current_datetime",
            description=description,
            parameters={
                "type": "object",
                "properties": {
                    "format": {
                        "type": ["string", "null"],
                        "description": "Python strftime format string.",
                    }
                },
                "required": ["format"],
                "additionalProperties": False,
            },
        )

    async def _handle(self, payload: dict[str, str], _: ToolContext) -> dict[str, str]:
        fmt = payload.get("format") or self._default_format
        timestamp = datetime.now(timezone.utc).strftime(fmt)
        return {"timestamp": timestamp}
