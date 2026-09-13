from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from llm_async.models import Tool
from llm_async.providers.openai_responses import OpenAIResponsesProvider


class PatchedOpenAIResponsesProvider(OpenAIResponsesProvider):
    def _format_tools(self, tools: Sequence[Any]) -> list[dict[str, Any]]:
        if not tools:
            return []
        function_tools: list[Tool] = []
        native_tools: list[dict[str, Any]] = []
        for tool in tools:
            if isinstance(tool, Tool):
                function_tools.append(tool)
            elif isinstance(tool, Mapping) and isinstance(tool.get("type"), str) and tool["type"].strip():
                native_tools.append(dict(tool))
        formatted = super()._format_tools(function_tools) if function_tools else []
        return [*formatted, *native_tools]
