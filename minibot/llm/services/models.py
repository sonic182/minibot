from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minibot.core.agent_runtime import ToolResult


@dataclass
class LLMGeneration:
    payload: Any
    response_id: str | None = None
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    status: str | None = None
    incomplete_reason: str | None = None


@dataclass
class LLMCompletionStep:
    message: Any
    response_id: str | None
    total_tokens: int | None = None


@dataclass
class ToolExecutionRecord:
    tool_name: str
    call_id: str
    message_payload: dict[str, Any]
    result: ToolResult


@dataclass
class LLMCompaction:
    response_id: str
    output: list[dict[str, Any]]
    total_tokens: int | None = None


@dataclass(frozen=True)
class UsageSnapshot:
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    status: str | None = None
    incomplete_reason: str | None = None
