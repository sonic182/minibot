from __future__ import annotations

from typing import Any

from minibot.core.agent_runtime import RuntimeLimits


def max_tool_iterations_from_client(llm_client: Any, *, default: int = 8) -> int:
    getter = getattr(llm_client, "max_tool_iterations", None)
    if callable(getter):
        maybe_value = getter()
        if isinstance(maybe_value, int) and maybe_value > 0:
            return maybe_value
    return default


def build_runtime_limits(
    *,
    llm_client: Any,
    timeout_seconds: int,
    min_timeout_seconds: int,
) -> RuntimeLimits:
    max_tool_iterations = max_tool_iterations_from_client(llm_client)
    return RuntimeLimits(
        max_steps=max_tool_iterations,
        max_tool_calls=max(12, max_tool_iterations * 2),
        timeout_seconds=max(min_timeout_seconds, int(timeout_seconds)),
    )
