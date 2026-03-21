from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from aiosonic import HeadersType  # type: ignore[import-untyped]
from llm_async.models import Response, Tool
from llm_async.models.response_schema import ResponseSchema
from llm_async.providers.openai_responses import OpenAIResponsesProvider
from llm_async.utils.http import post_json


class PatchedOpenAIResponsesProvider(OpenAIResponsesProvider):
    def _format_mixed_tools(self, tools: Sequence[Any] | None) -> list[dict[str, Any]]:
        if not tools:
            return []
        function_tools: list[Tool] = []
        native_tools: list[dict[str, Any]] = []
        for tool in tools:
            if isinstance(tool, Tool):
                function_tools.append(tool)
                continue
            if isinstance(tool, Mapping):
                tool_dict = dict(tool)
                if isinstance(tool_dict.get("type"), str) and tool_dict["type"].strip():
                    native_tools.append(tool_dict)
        formatted = super()._format_tools(function_tools) if function_tools else []
        return [*formatted, *native_tools]

    async def _single_complete(
        self,
        model: str | None,
        messages: list[dict[str, Any]],
        stream: bool = False,
        tools: list[Any] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_schema: ResponseSchema | Mapping[str, Any] | None = None,
        headers: HeadersType | None = None,
        **kwargs: Any,
    ) -> Response:
        previous_response_id = kwargs.pop("previous_response_id", None)

        payload: dict[str, Any] = {
            "stream": stream,
            **kwargs,
        }

        if model is not None:
            payload["model"] = model

        if previous_response_id:
            payload["previous_response_id"] = previous_response_id

        payload["input"] = self._messages_to_input(messages)

        schema_obj = ResponseSchema.coerce(response_schema)
        if schema_obj:
            payload["text"] = {"format": schema_obj.for_openai_responses()}

        formatted_tools = self._format_mixed_tools(tools)
        if formatted_tools:
            payload["tools"] = formatted_tools
        if tool_choice:
            payload["tool_choice"] = self._normalize_tool_choice(tool_choice)

        final_headers = self._headers_for_request(headers)

        if stream:
            return self._stream_responses_request(
                f"{self.base_url}/responses",
                payload,
                final_headers,
            )

        response = await post_json(
            self.client,
            f"{self.base_url}/responses",
            payload,
            final_headers,
            retry_config=self.retry_config,
        )
        main_response = self._parse_response(response)
        return Response(response, self.__class__.name(), main_response)
