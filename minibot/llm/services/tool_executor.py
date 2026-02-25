from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from llm_async.models.tool_call import ToolCall

from minibot.core.agent_runtime import ToolResult
from minibot.llm.services.models import ToolExecutionRecord
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.parse_utils import parse_json_maybe_python_object


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


def tool_name_from_call(call: ToolCall) -> str:
    if call.function:
        function_name = call.function.get("name")
        if isinstance(function_name, str) and function_name:
            return function_name
    if call.name:
        return call.name
    return "unknown_tool"


def parse_tool_call(call: ToolCall) -> tuple[str, dict[str, Any]]:
    if call.function:
        func_name = call.function.get("name")
        arguments = call.function.get("arguments")
        if isinstance(arguments, str):
            arguments_payload = arguments.strip()
            if not arguments_payload:
                arguments_dict = {}
            else:
                try:
                    arguments_dict = decode_tool_arguments(arguments_payload)
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


def decode_tool_arguments(arguments_payload: str) -> dict[str, Any]:
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
        parsed = parse_json_maybe_python_object(candidate)
        if parsed is None:
            continue
        if isinstance(parsed, dict):
            return dict(parsed)
    raise ValueError("Tool call arguments must be valid JSON")


def stringify_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, (list, dict)):
        return json.dumps(result, ensure_ascii=True, default=str)
    return str(result)


def normalize_tool_result(result: Any) -> ToolResult:
    if isinstance(result, ToolResult):
        return result
    return ToolResult(content=result)


def is_sensitive_argument_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    if not normalized:
        return False
    return any(part in normalized for part in _SENSITIVE_ARGUMENT_KEY_PARTS)


def sanitize_tool_arguments_for_log(arguments: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in arguments.items():
        key_text = str(key)
        if is_sensitive_argument_key(key_text):
            sanitized[key_text] = "***"
            continue
        sanitized[key_text] = sanitize_tool_argument_value(value)
    return sanitized


def sanitize_tool_argument_value(value: Any) -> Any:
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
        sanitized_list = [sanitize_tool_argument_value(item) for item in capped]
        if len(value) > _MAX_LOG_ARGUMENT_COLLECTION_ITEMS:
            sanitized_list.append(f"...(+{len(value) - _MAX_LOG_ARGUMENT_COLLECTION_ITEMS} items)")
        return sanitized_list
    if isinstance(value, dict):
        capped_items = list(value.items())[:_MAX_LOG_ARGUMENT_COLLECTION_ITEMS]
        sanitized_dict: dict[str, Any] = {}
        for item_key, item_value in capped_items:
            item_key_text = str(item_key)
            if is_sensitive_argument_key(item_key_text):
                sanitized_dict[item_key_text] = "***"
            else:
                sanitized_dict[item_key_text] = sanitize_tool_argument_value(item_value)
        if len(value) > _MAX_LOG_ARGUMENT_COLLECTION_ITEMS:
            sanitized_dict["..."] = f"+{len(value) - _MAX_LOG_ARGUMENT_COLLECTION_ITEMS} keys"
        return sanitized_dict
    return str(value)


async def execute_tool_calls_for_runtime(
    tool_calls: Sequence[ToolCall],
    tools: Sequence[ToolBinding],
    context: ToolContext,
    *,
    responses_mode: bool,
    logger: Any,
) -> list[ToolExecutionRecord]:
    tool_map = {binding.tool.name: binding for binding in tools}
    records: list[ToolExecutionRecord] = []
    for call in tool_calls:
        call_id = call.id
        if responses_mode and isinstance(call.input, dict):
            input_call_id = call.input.get("call_id")
            if isinstance(input_call_id, str) and input_call_id:
                call_id = input_call_id
        tool_name = tool_name_from_call(call)
        try:
            tool_name, arguments = parse_tool_call(call)
            binding = tool_map.get(tool_name)
            if not binding:
                raise ValueError(f"tool {tool_name} is not registered")
            logger.debug(
                "executing tool",
                extra={
                    "tool": tool_name,
                    "call_id": call_id,
                    "owner_id": context.owner_id,
                    "argument_keys": sorted(arguments.keys()),
                    "arguments": sanitize_tool_arguments_for_log(arguments),
                },
            )
            raw_result = await binding.handler(arguments, context)
            result = normalize_tool_result(raw_result)
            logger.debug(
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
            logger.exception(
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
                "output": stringify_result(result.content),
            }
        else:
            payload = {
                "role": "tool",
                "tool_call_id": call_id,
                "name": tool_name,
                "content": stringify_result(result.content),
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


async def execute_tool_calls(
    tool_calls: Sequence[ToolCall],
    tools: Sequence[ToolBinding],
    context: ToolContext,
    *,
    responses_mode: bool,
    logger: Any,
) -> list[dict[str, Any]]:
    records = await execute_tool_calls_for_runtime(
        tool_calls,
        tools,
        context,
        responses_mode=responses_mode,
        logger=logger,
    )
    return [record.message_payload for record in records]
