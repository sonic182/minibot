from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from llm_async.models.tool_call import ToolCall

from minibot.llm.services.reasoning_replay import apply_reasoning_replay, extract_reasoning_replay
from minibot.llm.services.tool_executor import decode_tool_arguments, stringify_result, tool_name_from_call

MAX_REPEATED_TOOL_ITERATIONS = 3


def any_tool_call_truncated(tool_calls: Sequence[Any]) -> bool:
    for call in tool_calls:
        fn = getattr(call, "function", None)
        if not isinstance(fn, dict):
            continue
        args = fn.get("arguments")
        if not isinstance(args, str) or not args.strip():
            continue
        try:
            decode_tool_arguments(args)
        except ValueError:
            return True
    return False


def assistant_message_for_followup(message: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": getattr(message, "role", "assistant") or "assistant",
        "content": getattr(message, "content", "") or "",
    }
    payload = apply_reasoning_replay(payload, extract_reasoning_replay(message))
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = [tool_call_to_payload(call) for call in tool_calls]
    return payload


def tool_call_to_payload(call: ToolCall) -> dict[str, Any]:
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


def tool_loop_fallback_payload(
    tool_messages: Sequence[dict[str, Any]],
    tool_names: Sequence[str],
) -> str:
    summary = summarize_tool_outputs(tool_messages)
    tools_used = ", ".join(tool_names[-4:]) if tool_names else "tools"
    return (
        "I executed tool calls but hit an internal tool-loop safeguard before finalizing. "
        f"Recent tools: {tools_used}. Last tool output: {summary}"
    )


def summarize_tool_outputs(tool_messages: Sequence[dict[str, Any]]) -> str:
    if not tool_messages:
        return "no tool output available"
    last = tool_messages[-1]
    output = last.get("output") if isinstance(last, dict) else None
    if output is None and isinstance(last, dict):
        output = last.get("content")
    if isinstance(output, str):
        return output[:400]
    return stringify_result(output)[:400]


def tool_iteration_signature(
    tool_calls: Sequence[ToolCall],
    tool_messages: Sequence[dict[str, Any]],
) -> str:
    parts: list[str] = []
    for index, call in enumerate(tool_calls):
        name = tool_name_from_call(call)
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
                    output = stringify_result(output_value)[:240]
        parts.append(f"{name}:{output}")
    return "|".join(parts)
