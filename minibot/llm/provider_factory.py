from __future__ import annotations

from typing import Any, Mapping, Sequence
import json

import logging

from llm_async.models.message import message_to_dict
from llm_async.models.tool_call import ToolCall
from llm_async.providers import ClaudeProvider, GoogleProvider, OpenAIProvider, OpenRouterProvider

from minibot.adapters.config.schema import LLMMConfig
from minibot.core.memory import MemoryEntry
from minibot.llm.tools.base import ToolBinding, ToolContext


LLM_PROVIDERS = {
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
    "google": GoogleProvider,
    "openrouter": OpenRouterProvider,
}


class LLMClient:
    def __init__(self, config: LLMMConfig) -> None:
        provider_cls = LLM_PROVIDERS.get(config.provider.lower(), OpenAIProvider)
        self._provider = provider_cls(api_key=config.api_key)
        self._model = config.model
        self._temperature = config.temperature
        self._max_new_tokens = config.max_new_tokens
        self._system_prompt = getattr(config, "system_prompt", "You are Minibot, a helpful assistant.")
        self._logger = logging.getLogger("minibot.llm")

    async def generate(
        self,
        history: Sequence[MemoryEntry],
        user_message: str,
        tools: Sequence[ToolBinding] | None = None,
        tool_context: ToolContext | None = None,
    ) -> str:
        messages = [
            {"role": "system", "content": self._system_prompt},
        ]
        messages.extend({"role": entry.role, "content": entry.content} for entry in history)
        messages.append({"role": "user", "content": user_message})

        if not self._provider.api_key:
            self._logger.warning("LLM provider key missing, falling back to echo", extra={"component": "llm"})
            return f"Echo: {user_message}"

        conversation = list(messages)
        tool_bindings = list(tools or [])
        tool_specs = [binding.tool for binding in tool_bindings] or None
        tool_map = {binding.tool.name: binding for binding in tool_bindings}
        context = tool_context or ToolContext()
        iterations = 0
        while True:
            response = await self._provider.acomplete(
                self._model,
                conversation,
                temperature=self._temperature,
                max_tokens=self._max_new_tokens,
                tools=tool_specs,
            )
            message = response.main_response
            if not message:
                raise RuntimeError("LLM did not return a completion")
            if not message.tool_calls or not tool_map:
                return message.content
            conversation.append(message.original or message_to_dict(message))
            tool_messages = await self._execute_tool_calls(message.tool_calls, tool_map, context)
            conversation.extend(tool_messages)
            iterations += 1
            if iterations >= 5:
                raise RuntimeError("tool call loop exceeded maximum iterations")

    async def _execute_tool_calls(
        self,
        tool_calls: Sequence[ToolCall],
        tool_map: Mapping[str, ToolBinding],
        context: ToolContext,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for call in tool_calls:
            tool_name, arguments = self._parse_tool_call(call)
            binding = tool_map.get(tool_name)
            if not binding:
                raise ValueError(f"tool {tool_name} is not registered")
            self._logger.info(
                "executing tool",
                extra={
                    "tool": tool_name,
                    "owner_id": context.owner_id,
                    "argument_keys": sorted(arguments.keys()),
                },
            )
            result = await binding.handler(arguments, context)
            self._logger.info(
                "tool execution completed",
                extra={
                    "tool": tool_name,
                    "owner_id": context.owner_id,
                },
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": tool_name,
                    "content": self._stringify_result(result),
                }
            )
        return messages

    def _parse_tool_call(self, call: ToolCall) -> tuple[str, dict[str, Any]]:
        if call.function:
            func_name = call.function.get("name")
            arguments = call.function.get("arguments")
            if isinstance(arguments, str):
                arguments_dict = json.loads(arguments or "{}")
            else:
                arguments_dict = dict(arguments or {})
        elif call.name:
            func_name = call.name
            arguments_dict = dict(call.input or {})
        else:
            raise ValueError("Tool call missing function metadata")
        if not func_name:
            raise ValueError("Tool call missing name")
        if not isinstance(arguments_dict, dict):
            raise ValueError("Tool call arguments must be an object")
        return func_name, arguments_dict

    def _stringify_result(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, (list, dict)):
            return json.dumps(result, default=str)
        return str(result)
