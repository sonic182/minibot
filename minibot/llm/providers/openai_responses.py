from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from llm_async.models import Message, Tool
from llm_async.providers.openai_responses import OpenAIResponsesProvider


def attach_reasoning_items(message: Message, original: Mapping[str, Any]) -> Message:
    """Keep the raw `reasoning` output items on the parsed message.

    The vendored parser reads `output` for text and tool calls only, dropping reasoning items —
    which arrive encrypted, so there is no text left to recover them from. They must be replayed
    verbatim before their `function_call` on the next request, and this is the one point both the
    streaming and non-streaming transports funnel their output items through (Codex only streams,
    so its `Response.original` stays empty and this is the sole chance to capture them).
    """
    output = original.get("output") if isinstance(original, Mapping) else None
    if not isinstance(output, list):
        return message
    reasoning_items = [dict(item) for item in output if isinstance(item, Mapping) and item.get("type") == "reasoning"]
    if reasoning_items:
        message.reasoning_details = reasoning_items
    return message


def messages_to_responses_input(messages: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Vendored `_messages_to_input`, plus reasoning replay.

    Shared by every provider built on `OpenAIResponsesProvider` — CodexProvider subclasses it
    directly, so its own `super()._messages_to_input` call skips PatchedOpenAIResponsesProvider
    entirely and would otherwise miss the fix.
    """
    if len(messages) == 1 and messages[0].get("role") == "user" and isinstance(messages[0].get("content"), str):
        return messages[0]["content"]

    responses_messages: list[dict[str, Any]] = []
    for msg in messages:
        msg_type = msg.get("type")

        if msg_type == "function_call_output":
            responses_messages.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.get("call_id", ""),
                    "output": msg.get("output", ""),
                }
            )
            continue

        role = msg.get("role")
        if role == "user":
            responses_messages.append({"role": "user", "content": msg.get("content", "")})
            continue

        if role != "assistant":
            continue

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            responses_messages.append({"role": "assistant", "content": msg.get("content", "")})
            continue

        for item in msg.get("reasoning_details") or []:
            if isinstance(item, Mapping) and item.get("type") == "reasoning":
                responses_messages.append(dict(item))

        for tc in tool_calls:
            fc_id = tc.get("id", "")
            call_id = tc.get("id", "")
            tc_input = tc.get("input", {})
            if isinstance(tc_input, dict) and "call_id" in tc_input:
                call_id = tc_input["call_id"]
            responses_messages.append(
                {
                    "type": "function_call",
                    "id": fc_id,
                    "call_id": call_id,
                    "name": (
                        tc.get("function", {}).get("name")
                        if isinstance(tc.get("function"), dict)
                        else tc.get("name", "")
                    ),
                    "arguments": (
                        tc.get("function", {}).get("arguments") if isinstance(tc.get("function"), dict) else ""
                    ),
                }
            )

    return responses_messages if responses_messages else messages


class PatchedOpenAIResponsesProvider(OpenAIResponsesProvider):
    def _messages_to_input(self, messages: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
        return messages_to_responses_input(messages)

    def _parse_response(self, original: dict[str, Any]) -> Message:
        return attach_reasoning_items(super()._parse_response(original), original)

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
