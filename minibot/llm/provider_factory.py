from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
import json

import logging

from llm_async.models.message import message_to_dict
from llm_async.models.tool_call import ToolCall
from llm_async.providers import ClaudeProvider, GoogleProvider, OpenAIProvider, OpenRouterProvider
from llm_async.providers.openai_responses import OpenAIResponsesProvider

from minibot.adapters.config.schema import LLMMConfig
from minibot.core.memory import MemoryEntry
from minibot.llm.tools.base import ToolBinding, ToolContext


LLM_PROVIDERS = {
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
    "google": GoogleProvider,
    "openrouter": OpenRouterProvider,
    "openai_responses": OpenAIResponsesProvider,
}


@dataclass
class LLMGeneration:
    payload: Any
    response_id: str | None = None


class LLMClient:
    def __init__(self, config: LLMMConfig) -> None:
        provider_cls = LLM_PROVIDERS.get(config.provider.lower(), OpenAIProvider)
        self._provider = provider_cls(api_key=config.api_key)
        self._model = config.model
        self._temperature = config.temperature
        self._send_temperature = getattr(config, "send_temperature", True)
        self._max_new_tokens = config.max_new_tokens
        self._max_tool_iterations = config.max_tool_iterations
        self._system_prompt = getattr(config, "system_prompt", "You are Minibot, a helpful assistant.")
        self._reasoning_effort = getattr(config, "reasoning_effort", "medium")
        self._is_responses_provider = isinstance(self._provider, OpenAIResponsesProvider)
        self._logger = logging.getLogger("minibot.llm")

    async def generate(
        self,
        history: Sequence[MemoryEntry],
        user_message: str,
        user_content: str | list[dict[str, Any]] | None = None,
        tools: Sequence[ToolBinding] | None = None,
        tool_context: ToolContext | None = None,
        response_schema: dict[str, Any] | None = None,
        prompt_cache_key: str | None = None,
        previous_response_id: str | None = None,
    ) -> LLMGeneration:
        messages = [
            {"role": "system", "content": self._system_prompt},
        ]
        messages.extend({"role": entry.role, "content": entry.content} for entry in history)
        final_user_content: str | list[dict[str, Any]] = user_message
        if user_content is not None:
            final_user_content = user_content
        messages.append({"role": "user", "content": final_user_content})

        if not self._provider.api_key:
            self._logger.warning("LLM provider key missing, falling back to echo", extra={"component": "llm"})
            return LLMGeneration(f"Echo: {user_message}")

        conversation = list(messages)
        tool_bindings = list(tools or [])
        tool_specs = [binding.tool for binding in tool_bindings] or None
        tool_map = {binding.tool.name: binding for binding in tool_bindings}
        context = tool_context or ToolContext()
        iterations = 0
        last_tool_messages: list[dict[str, Any]] = []
        recent_tool_names: list[str] = []
        extra_kwargs: dict[str, Any] = {}
        if prompt_cache_key and self._is_responses_provider:
            extra_kwargs["prompt_cache_key"] = prompt_cache_key
        if previous_response_id and self._is_responses_provider:
            extra_kwargs["previous_response_id"] = previous_response_id
        if self._is_responses_provider and self._reasoning_effort:
            extra_kwargs.setdefault("reasoning", {"effort": self._reasoning_effort})

        while True:
            call_kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": conversation,
                "tools": tool_specs,
                "response_schema": response_schema,
            }
            if self._send_temperature:
                call_kwargs["temperature"] = self._temperature
            if not self._is_responses_provider:
                call_kwargs["max_tokens"] = self._max_new_tokens
            call_kwargs.update(extra_kwargs)

            response = await self._provider.acomplete(**call_kwargs)
            message = response.main_response
            if not message:
                raise RuntimeError("LLM did not return a completion")
            if not message.tool_calls or not tool_map:
                payload = message.content
                response_id = self._extract_response_id(response)
                if response_schema and isinstance(payload, str):
                    try:
                        parsed = json.loads(payload)
                        return LLMGeneration(parsed, response_id)
                    except Exception:
                        self._logger.warning("failed to parse structured response; falling back to text")
                return LLMGeneration(payload, response_id)
            tool_messages = await self._execute_tool_calls(
                message.tool_calls,
                tool_map,
                context,
                responses_mode=self._is_responses_provider,
            )
            last_tool_messages = tool_messages
            recent_tool_names.extend(self._tool_name_from_call(call) for call in message.tool_calls)
            if self._is_responses_provider:
                response_id = self._extract_response_id(response)
                if response_id:
                    extra_kwargs["previous_response_id"] = response_id
                conversation = tool_messages
            else:
                conversation.append(message.original or message_to_dict(message))
                conversation.extend(tool_messages)
            iterations += 1
            if iterations >= self._max_tool_iterations:
                self._logger.warning(
                    "tool call loop exceeded maximum iterations; returning fallback",
                    extra={"tool_names": recent_tool_names[-10:]},
                )
                response_id = self._extract_response_id(response)
                payload = self._tool_loop_fallback_payload(last_tool_messages, recent_tool_names, response_schema)
                return LLMGeneration(payload, response_id)

    def is_responses_provider(self) -> bool:
        return self._is_responses_provider

    @staticmethod
    def _extract_response_id(response: Any) -> str | None:
        original = getattr(response, "original", None)
        if isinstance(original, dict):
            resp_id = original.get("id")
            if isinstance(resp_id, str):
                return resp_id
        return None

    async def _execute_tool_calls(
        self,
        tool_calls: Sequence[ToolCall],
        tool_map: Mapping[str, ToolBinding],
        context: ToolContext,
        responses_mode: bool = False,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for call in tool_calls:
            tool_name = self._tool_name_from_call(call)
            try:
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
            except Exception as exc:
                self._logger.exception(
                    "tool execution failed",
                    extra={
                        "tool": tool_name,
                        "owner_id": context.owner_id,
                    },
                )
                result = {
                    "ok": False,
                    "tool": tool_name,
                    "error": str(exc),
                }
            if responses_mode:
                call_id = call.id
                if isinstance(call.input, dict):
                    input_call_id = call.input.get("call_id")
                    if isinstance(input_call_id, str) and input_call_id:
                        call_id = input_call_id
                messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": self._stringify_result(result),
                    }
                )
            else:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": tool_name,
                        "content": self._stringify_result(result),
                    }
                )
        return messages

    @staticmethod
    def _tool_name_from_call(call: ToolCall) -> str:
        if call.function:
            function_name = call.function.get("name")
            if isinstance(function_name, str) and function_name:
                return function_name
        if call.name:
            return call.name
        return "unknown_tool"

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

    def _tool_loop_fallback_payload(
        self,
        tool_messages: Sequence[dict[str, Any]],
        tool_names: Sequence[str],
        response_schema: dict[str, Any] | None,
    ) -> Any:
        summary = self._summarize_tool_outputs(tool_messages)
        tools_used = ", ".join(tool_names[-4:]) if tool_names else "tools"
        answer = (
            "I executed tool calls but hit an internal tool-loop safeguard before finalizing. "
            f"Recent tools: {tools_used}. Last tool output: {summary}"
        )
        if response_schema:
            return {
                "answer": answer,
                "should_answer_to_user": True,
            }
        return answer

    def _summarize_tool_outputs(self, tool_messages: Sequence[dict[str, Any]]) -> str:
        if not tool_messages:
            return "no tool output available"
        last = tool_messages[-1]
        output = last.get("output") if isinstance(last, dict) else None
        if output is None and isinstance(last, dict):
            output = last.get("content")
        if isinstance(output, str):
            return output[:400]
        return self._stringify_result(output)[:400]
