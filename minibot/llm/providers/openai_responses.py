from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from aiosonic import HeadersType  # type: ignore[import-untyped]
from llm_async.models import Response, Tool
from llm_async.models.response import StreamChunk
from llm_async.providers.openai_responses import OpenAIResponsesProvider
from llm_async.utils.http import stream_json

from minibot.llm.errors import ProviderResponseError

_TERMINAL_EVENTS = {"response.completed", "response.incomplete", "response.failed"}


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

    def _stream_responses_request(self, url: str, payload: dict[str, Any], headers: HeadersType) -> Response:
        response = Response({}, self.__class__.name(), stream=True, stream_generator=None)

        async def _gen():
            accumulated_items: list[dict[str, Any]] = []
            async for chunk in stream_json(self.client, url, payload, headers, retry_config=self.retry_config):
                if not isinstance(chunk, dict):
                    continue
                chunk_type = chunk.get("type")
                if chunk_type == "response.output_item.done" and isinstance(chunk.get("item"), dict):
                    accumulated_items.append(chunk["item"])
                elif chunk_type in _TERMINAL_EVENTS and isinstance(chunk.get("response"), dict):
                    response.original = {**chunk["response"], "output": accumulated_items}
                elif chunk_type == "error":
                    response.original = {
                        "status": "failed",
                        "error": {key: chunk.get(key) for key in ("code", "type", "message")},
                        "output": accumulated_items,
                    }
                delta_text = self._extract_stream_text(chunk)
                if delta_text:
                    yield StreamChunk(delta_text, chunk)
            if response.original.get("status") == "failed":
                raise ProviderResponseError.from_payload(response.original)
            response.main_response = self._parse_response(response.original or {"output": accumulated_items})

        response.stream_generator = _gen()
        return response
