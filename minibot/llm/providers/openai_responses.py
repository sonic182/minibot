from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from aiosonic import HeadersType  # type: ignore[import-untyped]
from llm_async.models import Response, Tool
from llm_async.models.response import StreamChunk
from llm_async.providers.openai_responses import OpenAIResponsesProvider
from llm_async.utils.http import stream_json

from minibot.llm.errors import ProviderResponseError
from minibot.llm.tools.schema_utils import with_object_properties

_TERMINAL_EVENTS = {"response.completed", "response.incomplete", "response.failed"}
_RAW_ERROR_LOG_CHARS = 2000
_LOGGER = logging.getLogger("minibot.llm")


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
        for tool in formatted:
            if isinstance(tool.get("parameters"), dict):
                tool["parameters"] = with_object_properties(tool["parameters"])
        return [*formatted, *native_tools]

    def _messages_to_input(self, messages: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
        system_indexes = [index for index, message in enumerate(messages) if message.get("role") == "system"]
        if len(system_indexes) < 2:
            return super()._messages_to_input(messages)
        items: list[dict[str, Any]] = []
        chunk: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            if message.get("role") != "system":
                chunk.append(message)
                continue
            items.extend(self._convert_chunk(chunk))
            chunk = []
            if index != system_indexes[0]:
                developer_item = _developer_item(message.get("content"))
                if developer_item is not None:
                    items.append(developer_item)
        items.extend(self._convert_chunk(chunk))
        return items

    def _convert_chunk(self, chunk: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not chunk:
            return []
        converted = super()._messages_to_input(chunk)
        if isinstance(converted, str):
            return [{"role": "user", "content": converted}]
        return converted

    def _stream_responses_request(self, url: str, payload: dict[str, Any], headers: HeadersType) -> Response:
        response = Response({}, self.__class__.name(), stream=True, stream_generator=None)

        async def _gen():
            accumulated_items: list[dict[str, Any]] = []
            response_id: str | None = None
            async for chunk in stream_json(self.client, url, payload, headers, retry_config=self.retry_config):
                if not isinstance(chunk, dict):
                    continue
                chunk_type = chunk.get("type")
                if isinstance(chunk.get("response"), dict) and isinstance(chunk["response"].get("id"), str):
                    response_id = chunk["response"]["id"]
                if chunk_type == "response.output_item.done" and isinstance(chunk.get("item"), dict):
                    accumulated_items.append(chunk["item"])
                elif chunk_type in _TERMINAL_EVENTS and isinstance(chunk.get("response"), dict):
                    response.original = {**chunk["response"], "output": accumulated_items}
                elif chunk_type == "error":
                    _LOGGER.warning(
                        "provider stream error event",
                        extra={
                            "response_id": response_id,
                            "raw_event": json.dumps(chunk, default=str)[:_RAW_ERROR_LOG_CHARS],
                        },
                    )
                    nested = chunk.get("error")
                    source = nested if isinstance(nested, dict) else chunk
                    response.original = {
                        "id": response_id,
                        "status": "failed",
                        "error": {key: source.get(key) for key in ("code", "type", "message", "param")},
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


def _developer_item(content: Any) -> dict[str, Any] | None:
    if isinstance(content, str):
        if not content.strip():
            return None
        return {"role": "developer", "content": [{"type": "input_text", "text": content}]}
    if isinstance(content, list) and content:
        return {"role": "developer", "content": content}
    return None
