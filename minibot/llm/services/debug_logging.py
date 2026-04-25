from __future__ import annotations

import logging
from typing import Any


def log_provider_response(
    *,
    logger: logging.Logger,
    response: Any,
    context: str,
    provider_name: str,
    strip_logs: bool = False,
) -> None:
    original = getattr(response, "original", None)
    response_id = getattr(response, "response_id", None) or getattr(response, "id", None)
    if response_id is None and isinstance(original, dict):
        response_id = original.get("id")
    message = getattr(response, "main_response", None)
    content = getattr(message, "content", None) if message is not None else None
    tool_calls = getattr(message, "tool_calls", None) if message is not None else None

    logger.debug(
        "provider raw response",
        extra={
            "context": context,
            "provider": provider_name,
            "response_id": response_id,
            "response_original_present": bool(original),
            "message_content_type": type(content).__name__ if content is not None else "NoneType",
            "message_tool_calls_count": len(tool_calls or []),
            "message_tool_call_names": [
                item["name"] for item in (_serialize_tool_call(tc) for tc in (tool_calls or []))
            ],
            "logs_stripped": strip_logs,
        },
    )


def _serialize_tool_call(tc: Any) -> dict[str, Any]:
    name = getattr(tc, "name", None)
    if name is None:
        fn = getattr(tc, "function", None)
        if isinstance(fn, dict):
            name = fn.get("name")
    arguments = getattr(tc, "input", None) or getattr(tc, "arguments", None)
    if arguments is None:
        fn = getattr(tc, "function", None)
        if isinstance(fn, dict):
            arguments = fn.get("arguments")
    return {"id": getattr(tc, "id", None), "name": name, "arguments_present": arguments is not None}
