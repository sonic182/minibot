from __future__ import annotations

import json
import logging
from typing import Any

_MAX_PREVIEW_CHARS = 4000
_STRIPPED_RESPONSE_ORIGINAL_CHARS = 70


def log_provider_response(
    *,
    logger: logging.Logger,
    response: Any,
    context: str,
    provider_name: str,
    strip_logs: bool = False,
) -> None:
    original = getattr(response, "original", None)
    message = getattr(response, "main_response", None)
    message_original = getattr(message, "original", None) if message is not None else None
    content = getattr(message, "content", None) if message is not None else None
    tool_calls = getattr(message, "tool_calls", None) if message is not None else None

    logger.debug(
        "provider raw response",
        extra={
            "context": context,
            "provider": provider_name,
            "response_original": _dump_response_original(original, strip_logs=strip_logs),
            "message_original": _safe_dump(message_original),
            "message_content_type": type(content).__name__ if content is not None else "NoneType",
            "message_content_preview": _preview_content(content),
            "message_tool_calls_count": len(tool_calls or []),
            "message_tool_calls": _safe_dump([_serialize_tool_call(tc) for tc in (tool_calls or [])]),
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
    return {"id": getattr(tc, "id", None), "name": name, "arguments": arguments}


def _preview_content(content: Any) -> str:
    if isinstance(content, str):
        return _truncate(content)
    if isinstance(content, list):
        return _truncate(_safe_dump(content))
    if content is None:
        return ""
    return _truncate(str(content))


def _dump_response_original(value: Any, *, strip_logs: bool) -> str:
    if not strip_logs:
        return _safe_dump(value)
    return _safe_dump(value, max_chars=_STRIPPED_RESPONSE_ORIGINAL_CHARS, suffix="...")


def _safe_dump(value: Any, *, max_chars: int = _MAX_PREVIEW_CHARS, suffix: str = "...<truncated>") -> str:
    try:
        return _truncate(json.dumps(value, ensure_ascii=True, default=str), max_chars=max_chars, suffix=suffix)
    except Exception:
        return _truncate(str(value), max_chars=max_chars, suffix=suffix)


def _truncate(value: str, *, max_chars: int = _MAX_PREVIEW_CHARS, suffix: str = "...<truncated>") -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}{suffix}"
