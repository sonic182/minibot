from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
import json
import re

import logging
import yaml

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
        configured_provider = config.provider.lower()
        provider_cls = LLM_PROVIDERS.get(configured_provider, OpenAIProvider)
        self._provider = provider_cls(api_key=config.api_key)
        self._provider_name = configured_provider if configured_provider in LLM_PROVIDERS else "openai"
        self._model = config.model
        self._temperature = config.temperature
        self._send_temperature = getattr(config, "send_temperature", True)
        self._send_reasoning_effort = getattr(config, "send_reasoning_effort", True)
        self._max_new_tokens = config.max_new_tokens
        self._max_tool_iterations = config.max_tool_iterations
        self._system_prompt = getattr(config, "system_prompt", "You are Minibot, a helpful assistant.")
        self._reasoning_effort = getattr(config, "reasoning_effort", "medium")
        self._openrouter_models = list(getattr(getattr(config, "openrouter", None), "models", []) or [])
        self._openrouter_provider = self._build_openrouter_provider_payload(config)
        self._openrouter_plugins = list(getattr(getattr(config, "openrouter", None), "plugins", []) or [])
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
        tool_choice_override: str | dict[str, Any] | None = None
        tool_intent_retry_attempted = False
        continue_loop_retry_attempted = False
        last_tool_messages: list[dict[str, Any]] = []
        recent_tool_names: list[str] = []
        extra_kwargs: dict[str, Any] = {}
        if prompt_cache_key and self._is_responses_provider:
            extra_kwargs["prompt_cache_key"] = prompt_cache_key
        if previous_response_id and self._is_responses_provider:
            extra_kwargs["previous_response_id"] = previous_response_id
        if self._is_responses_provider and self._send_reasoning_effort and self._reasoning_effort:
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
            if tool_choice_override is not None:
                call_kwargs["tool_choice"] = tool_choice_override
            call_kwargs.update(self._openrouter_kwargs())
            call_kwargs.update(extra_kwargs)

            response = await self._provider.acomplete(**call_kwargs)
            message = response.main_response
            if not message:
                raise RuntimeError("LLM did not return a completion")
            if not message.tool_calls or not tool_map:
                payload = message.content
                parsed_payload = self._coerce_payload_dict(payload)
                continue_loop_hint = self._continue_loop_requested(parsed_payload)
                if tool_map and continue_loop_hint and not continue_loop_retry_attempted:
                    continue_loop_retry_attempted = True
                    tool_choice_override = "required"
                    self._logger.info(
                        "retrying completion because continue_loop was requested",
                        extra={"provider": self._provider_name, "model": self._model},
                    )
                    continue
                if (
                    tool_map
                    and not message.tool_calls
                    and not tool_intent_retry_attempted
                    and self._is_explicit_tool_request(user_message)
                ):
                    tool_intent_retry_attempted = True
                    tool_choice_override = "required"
                    self._logger.info(
                        "retrying completion with required tool choice",
                        extra={"provider": self._provider_name, "model": self._model},
                    )
                    continue
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
                conversation.append(self._assistant_message_for_followup(message))
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

    def provider_name(self) -> str:
        return self._provider_name

    def model_name(self) -> str:
        return self._model

    def supports_media_inputs(self) -> bool:
        return self._provider_name in {"openai_responses", "openai", "openrouter"}

    def media_input_mode(self) -> str:
        if self._provider_name == "openai_responses":
            return "responses"
        if self._provider_name in {"openai", "openrouter"}:
            return "chat_completions"
        return "none"

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
                error_code = "tool_execution_failed"
                if isinstance(exc, ValueError) and "arguments" in str(exc).lower():
                    error_code = "invalid_tool_arguments"
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
                    "error_code": error_code,
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
                arguments_payload = arguments.strip()
                if not arguments_payload:
                    arguments_dict = {}
                else:
                    try:
                        arguments_dict = self._decode_tool_arguments(arguments_payload)
                    except ValueError as exc:
                        preview = arguments_payload.replace("\n", " ")
                        if len(preview) > 220:
                            preview = f"{preview[:220]}..."
                        raise ValueError(
                            "Tool call arguments must be a valid JSON object. "
                            f"Received arguments preview: {preview}"
                        ) from exc
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

    @staticmethod
    def _decode_tool_arguments(arguments_payload: str) -> dict[str, Any]:
        candidates = [arguments_payload]
        stripped = arguments_payload.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            fenced = "\n".join(lines).strip()
            if fenced:
                candidates.append(fenced)
        repaired_candidates: list[str] = []
        for candidate in candidates:
            text = candidate.strip()
            if text.startswith("{"):
                missing = text.count("{") - text.count("}")
                if missing > 0:
                    repaired_candidates.append(text + ("}" * missing))
        candidates.extend(repaired_candidates)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(candidate)
                except (ValueError, SyntaxError):
                    continue
            if isinstance(parsed, dict):
                return dict(parsed)
        raise ValueError("Tool call arguments must be valid JSON")

    @staticmethod
    def _is_explicit_tool_request(user_message: str) -> bool:
        text = user_message.strip().lower()
        if not text:
            return False
        return bool(
            re.search(
                r"\b(use|using|call|execute|run|invoke|do)\b.{0,40}\b(tool|browser|http|datetime|current time)\b",
                text,
            )
            or re.search(r"\bwith\s+tool\b", text)
            or re.search(r"\busing\s+tool\b", text)
        )

    @staticmethod
    def _coerce_payload_dict(payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, dict):
            return payload
        if not isinstance(payload, str):
            return None
        text = payload.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                return None
        if isinstance(parsed, dict):
            return parsed
        return None

    @staticmethod
    def _continue_loop_requested(payload: dict[str, Any] | None) -> bool:
        if not isinstance(payload, dict):
            return False
        value = payload.get("continue_loop")
        return isinstance(value, bool) and value

    def _stringify_result(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, (list, dict)):
            try:
                return yaml.safe_dump(
                    result,
                    sort_keys=False,
                    allow_unicode=False,
                    default_flow_style=False,
                    width=1000,
                )
            except Exception:
                return json.dumps(result, default=str)
        return str(result)

    @staticmethod
    def _build_openrouter_provider_payload(config: LLMMConfig) -> dict[str, Any]:
        provider_cfg = getattr(getattr(config, "openrouter", None), "provider", None)
        if provider_cfg is None:
            return {}

        payload: dict[str, Any] = dict(getattr(provider_cfg, "provider_extra", {}) or {})
        typed_fields = (
            "order",
            "allow_fallbacks",
            "require_parameters",
            "data_collection",
            "zdr",
            "enforce_distillable_text",
            "only",
            "ignore",
            "quantizations",
            "sort",
            "preferred_min_throughput",
            "preferred_max_latency",
            "max_price",
        )
        for field_name in typed_fields:
            value = getattr(provider_cfg, field_name, None)
            if value is not None:
                payload[field_name] = value
        return payload

    def _openrouter_kwargs(self) -> dict[str, Any]:
        if self._provider_name != "openrouter":
            return {}
        kwargs: dict[str, Any] = {}
        if self._openrouter_models:
            kwargs["models"] = self._openrouter_models
        if self._openrouter_provider:
            kwargs["provider"] = self._openrouter_provider
        if self._openrouter_plugins:
            kwargs["plugins"] = self._openrouter_plugins
        return kwargs

    @staticmethod
    def _assistant_message_for_followup(message: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": getattr(message, "role", "assistant") or "assistant",
            "content": getattr(message, "content", "") or "",
        }
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            payload["tool_calls"] = [LLMClient._tool_call_to_payload(call) for call in tool_calls]
        return payload

    @staticmethod
    def _tool_call_to_payload(call: ToolCall) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": call.id,
            "type": call.type,
        }
        if call.function is not None:
            payload["function"] = call.function
        if call.name is not None:
            payload["name"] = call.name
        if call.input is not None:
            payload["input"] = call.input
        return payload

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
