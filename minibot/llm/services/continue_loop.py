from __future__ import annotations

from typing import Any


CONTINUE_LOOP_RETRY_PATCH = (
    "Do not describe intended tool use. Either call a tool now using native tool calling, "
    "or return a final structured response with continue_loop=false."
)


def should_continue_tool_loop(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("continue_loop") is not True:
        return False
    return payload.get("should_answer_to_user") is False
