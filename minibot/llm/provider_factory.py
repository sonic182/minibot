from __future__ import annotations

import ast
from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Sequence

import logging
import aiosonic

from llm_async.models.tool_call import ToolCall
from llm_async.models import Tool
from llm_async.providers import ClaudeProvider, GoogleProvider, OpenAIProvider, OpenRouterProvider
from llm_async.providers.openai_responses import OpenAIResponsesProvider
from llm_async.utils.retry import RetryConfig

from minibot.adapters.config.schema import LLMMConfig
from minibot.core.memory import MemoryEntry
from minibot.core.agent_runtime import ToolResult
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.json_schema import to_openai_strict_schema


LLM_PROVIDERS = {
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
    "google": GoogleProvider,
    "openrouter": OpenRouterProvider,
    "openai_responses": OpenAIResponsesProvider,
}

_MAX_REPEATED_TOOL_ITERATIONS = 3
_MAX_LOG_ARGUMENT_STRING_CHARS = 300
_MAX_LOG_ARGUMENT_COLLECTION_ITEMS = 20
_SENSITIVE_ARGUMENT_KEY_PARTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "cookie",
)
_OPENAI_STRICT_MODEL_PATTERNS = (
    re.compile(r"^openai(?:/.*)?"),
    re.compile(r"^gpt-.*"),
)


@dataclass
class LLMGeneration:
    payload: Any
    response_id: str | None = None


@dataclass
class LLMCompletionStep:
    message: Any
    response_id: str | None


@dataclass
class ToolExecutionRecord:
    tool_name: str
    call_id: str
    message_payload: dict[str, Any]
    result: ToolResult


class LLMClient:
    def __init__(self, config: LLMMConfig) -> None:
        configured_provider = config.provider.lower()
        provider_cls = LLM_PROVIDERS.get(configured_provider, OpenAIProvider)
        timeouts = aiosonic.Timeouts(
            sock_connect=float(config.sock_connect_timeout_seconds),
            sock_read=float(config.sock_read_timeout_seconds),
            request_timeout=float(config.request_timeout_seconds),
        )
        connector = aiosonic.TCPConnector(timeouts=timeouts)
        retry_config = RetryConfig(
            max_attempts=config.retry_attempts + 1,
            base_delay=float(config.retry_delay_seconds),
            max_delay=float(config.retry_delay_seconds),
            backoff_factor=1.0,
            jitter=False,
        )
        provider_kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "retry_config": retry_config,
            "client_kwargs": {"connector": connector},
        }
        if config.base_url:
            provider_kwargs["base_url"] = config.base_url
        self._provider = provider_cls(**provider_kwargs)
        self._provider_name = configured_provider if configured_provider in LLM_PROVIDERS else "openai"
        self._model = config.model
        self._temperature = config.temperature
        self._max_new_tokens = config.max_new_tokens
        self._max_tool_iterations = config.max_tool_iterations
        self._system_prompt = getattr(config, "system_prompt", "You are Minibot, a helpful assistant.")
        self._prompts_dir = getattr(config, "prompts_dir", "./prompts")
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
        system_prompt_override: str | None = None,
    ) -> LLMGeneration:
        system_prompt = system_prompt_override or self._system_prompt
        messages = [
            {"role": "system", "content": system_prompt},
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
        tool_specs = self._prepare_tool_specs(tool_bindings)
        tool_map = {binding.tool.name: binding for binding in tool_bindings}
        context = tool_context or ToolContext()
        iterations = 0
        last_tool_messages: list[dict[str, Any]] = []
        recent_tool_names: list[str] = []
        last_iteration_signature: str | None = None
        repeated_iteration_count = 0
        extra_kwargs: dict[str, Any] = {}
        strict_response_schema = response_schema
        if isinstance(response_schema, dict) and self._should_apply_openai_strict_schema(self._model):
            strict_response_schema = to_openai_strict_schema(response_schema)
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
                "response_schema": strict_response_schema,
            }
            if self._temperature is not None:
                call_kwargs["temperature"] = self._temperature
            resolved_max_tokens = self._resolved_max_tokens()
            if resolved_max_tokens is not None:
                call_kwargs["max_tokens"] = resolved_max_tokens
            call_kwargs.update(self._openrouter_kwargs())
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
                        parsed = self._parse_structured_payload(payload)
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
            iteration_signature = self._tool_iteration_signature(message.tool_calls, tool_messages)
            if iteration_signature and iteration_signature == last_iteration_signature:
                repeated_iteration_count += 1
            else:
                repeated_iteration_count = 1
            last_iteration_signature = iteration_signature
            last_tool_messages = tool_messages
            recent_tool_names.extend(self._tool_name_from_call(call) for call in message.tool_calls)
            if repeated_iteration_count >= _MAX_REPEATED_TOOL_ITERATIONS:
                self._logger.warning(
                    "tool loop repeated identical outputs; returning fallback",
                    extra={
                        "tool_names": recent_tool_names[-10:],
                        "repeated_count": repeated_iteration_count,
                    },
                )
                response_id = self._extract_response_id(response)
                payload = self._tool_loop_fallback_payload(last_tool_messages, recent_tool_names, response_schema)
                return LLMGeneration(payload, response_id)
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

    async def complete_once(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolBinding] | None = None,
        response_schema: dict[str, Any] | None = None,
        prompt_cache_key: str | None = None,
        previous_response_id: str | None = None,
    ) -> LLMCompletionStep:
        tool_specs = self._prepare_tool_specs(tools or [])
        strict_response_schema = response_schema
        if isinstance(response_schema, dict) and self._should_apply_openai_strict_schema(self._model):
            strict_response_schema = to_openai_strict_schema(response_schema)
        call_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "tools": tool_specs,
            "response_schema": strict_response_schema,
        }
        if self._temperature is not None:
            call_kwargs["temperature"] = self._temperature
        resolved_max_tokens = self._resolved_max_tokens()
        if resolved_max_tokens is not None:
            call_kwargs["max_tokens"] = resolved_max_tokens
        call_kwargs.update(self._openrouter_kwargs())
        if prompt_cache_key and self._is_responses_provider:
            call_kwargs["prompt_cache_key"] = prompt_cache_key
        if previous_response_id and self._is_responses_provider:
            call_kwargs["previous_response_id"] = previous_response_id
        if self._is_responses_provider and self._reasoning_effort:
            call_kwargs.setdefault("reasoning", {"effort": self._reasoning_effort})

        response = await self._provider.acomplete(**call_kwargs)
        message = response.main_response
        if not message:
            raise RuntimeError("LLM did not return a completion")
        return LLMCompletionStep(message=message, response_id=self._extract_response_id(response))

    async def execute_tool_calls_for_runtime(
        self,
        tool_calls: Sequence[ToolCall],
        tools: Sequence[ToolBinding],
        context: ToolContext,
        responses_mode: bool = False,
    ) -> list[ToolExecutionRecord]:
        tool_map = {binding.tool.name: binding for binding in tools}
        records: list[ToolExecutionRecord] = []
        for call in tool_calls:
            call_id = call.id
            if responses_mode and isinstance(call.input, dict):
                input_call_id = call.input.get("call_id")
                if isinstance(input_call_id, str) and input_call_id:
                    call_id = input_call_id
            tool_name = self._tool_name_from_call(call)
            try:
                tool_name, arguments = self._parse_tool_call(call)
                binding = tool_map.get(tool_name)
                if not binding:
                    raise ValueError(f"tool {tool_name} is not registered")
                self._logger.debug(
                    "executing tool",
                    extra={
                        "tool": tool_name,
                        "call_id": call_id,
                        "owner_id": context.owner_id,
                        "argument_keys": sorted(arguments.keys()),
                        "arguments": self._sanitize_tool_arguments_for_log(arguments),
                    },
                )
                raw_result = await binding.handler(arguments, context)
                result = self._normalize_tool_result(raw_result)
                self._logger.debug(
                    "tool execution completed",
                    extra={
                        "tool": tool_name,
                        "call_id": call_id,
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
                result = ToolResult(
                    content={
                        "ok": False,
                        "tool": tool_name,
                        "error_code": error_code,
                        "error": str(exc),
                    }
                )
            if responses_mode:
                payload = {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": self._stringify_result(result.content),
                }
            else:
                payload = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": tool_name,
                    "content": self._stringify_result(result.content),
                }
            records.append(
                ToolExecutionRecord(
                    tool_name=tool_name,
                    call_id=call_id,
                    message_payload=payload,
                    result=result,
                )
            )
        return records

    def provider_name(self) -> str:
        return self._provider_name

    def model_name(self) -> str:
        return self._model

    def system_prompt(self) -> str:
        return self._system_prompt

    def prompts_dir(self) -> str:
        return self._prompts_dir

    def max_tool_iterations(self) -> int:
        return self._max_tool_iterations

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
        records = await self.execute_tool_calls_for_runtime(
            tool_calls,
            list(tool_map.values()),
            context,
            responses_mode=responses_mode,
        )
        return [record.message_payload for record in records]

    @staticmethod
    def _normalize_tool_result(result: Any) -> ToolResult:
        if isinstance(result, ToolResult):
            return result
        return ToolResult(content=result)

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
                            f"Tool call arguments must be a valid JSON object. Received arguments preview: {preview}"
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

    def _stringify_result(self, result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, (list, dict)):
            return json.dumps(result, ensure_ascii=True, default=str)
        return str(result)

    @staticmethod
    def _sanitize_tool_arguments_for_log(arguments: Mapping[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in arguments.items():
            key_text = str(key)
            if LLMClient._is_sensitive_argument_key(key_text):
                sanitized[key_text] = "***"
                continue
            sanitized[key_text] = LLMClient._sanitize_tool_argument_value(value)
        return sanitized

    @staticmethod
    def _sanitize_tool_argument_value(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) <= _MAX_LOG_ARGUMENT_STRING_CHARS:
                return value
            return f"{value[:_MAX_LOG_ARGUMENT_STRING_CHARS]}..."
        if isinstance(value, list):
            capped = value[:_MAX_LOG_ARGUMENT_COLLECTION_ITEMS]
            sanitized_list = [LLMClient._sanitize_tool_argument_value(item) for item in capped]
            if len(value) > _MAX_LOG_ARGUMENT_COLLECTION_ITEMS:
                sanitized_list.append(f"...(+{len(value) - _MAX_LOG_ARGUMENT_COLLECTION_ITEMS} items)")
            return sanitized_list
        if isinstance(value, dict):
            capped_items = list(value.items())[:_MAX_LOG_ARGUMENT_COLLECTION_ITEMS]
            sanitized_dict: dict[str, Any] = {}
            for item_key, item_value in capped_items:
                item_key_text = str(item_key)
                if LLMClient._is_sensitive_argument_key(item_key_text):
                    sanitized_dict[item_key_text] = "***"
                else:
                    sanitized_dict[item_key_text] = LLMClient._sanitize_tool_argument_value(item_value)
            if len(value) > _MAX_LOG_ARGUMENT_COLLECTION_ITEMS:
                sanitized_dict["..."] = f"+{len(value) - _MAX_LOG_ARGUMENT_COLLECTION_ITEMS} keys"
            return sanitized_dict
        return str(value)

    def _prepare_tool_specs(self, tool_bindings: Sequence[ToolBinding]) -> list[Tool] | None:
        if not tool_bindings:
            return None
        if not self._should_apply_openai_strict_schema(self._model):
            return [binding.tool for binding in tool_bindings]
        strict_tools: list[Tool] = []
        for binding in tool_bindings:
            parameters = binding.tool.parameters
            if isinstance(parameters, dict):
                parameters = to_openai_strict_schema(parameters)
            strict_tools.append(
                Tool(
                    name=binding.tool.name,
                    description=binding.tool.description,
                    parameters=parameters,
                )
            )
        return strict_tools

    @staticmethod
    def _is_sensitive_argument_key(key: str) -> bool:
        normalized = key.strip().lower().replace("-", "_")
        if not normalized:
            return False
        return any(part in normalized for part in _SENSITIVE_ARGUMENT_KEY_PARTS)

    @staticmethod
    def _should_apply_openai_strict_schema(model_name: str | None) -> bool:
        if not isinstance(model_name, str) or not model_name:
            return False
        return any(pattern.match(model_name) for pattern in _OPENAI_STRICT_MODEL_PATTERNS)

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

    def _resolved_max_tokens(self) -> int | None:
        if self._is_responses_provider:
            return None
        if self._provider_name == "openrouter":
            if self._max_new_tokens is None:
                return 4096
            return min(self._max_new_tokens, 32768)
        if self._max_new_tokens is not None:
            return self._max_new_tokens
        return None

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

    def _tool_iteration_signature(
        self,
        tool_calls: Sequence[ToolCall],
        tool_messages: Sequence[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        for index, call in enumerate(tool_calls):
            name = self._tool_name_from_call(call)
            output = ""
            if index < len(tool_messages):
                message = tool_messages[index]
                if isinstance(message, dict):
                    output_value = message.get("output")
                    if output_value is None:
                        output_value = message.get("content")
                    if isinstance(output_value, str):
                        output = output_value[:240]
                    else:
                        output = self._stringify_result(output_value)[:240]
            parts.append(f"{name}:{output}")
        return "|".join(parts)

    @staticmethod
    def _parse_structured_payload(payload: str) -> Any:
        try:
            return json.loads(payload)
        except Exception:
            stripped = payload.strip()
            stripped = re.sub(r"^```json\s*", "", stripped, flags=re.IGNORECASE)
            stripped = re.sub(r"^```\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
            return json.loads(stripped)
