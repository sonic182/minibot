"""Render a tool call as one short line for a terminal transcript.

``ToolCallEvent`` carries arguments in full, values included. Everything user-facing goes through
here, which makes this module the redaction boundary: secret-ish arguments are masked and long ones
are clipped, so no credential or multi-kilobyte payload reaches the screen.

The per-tool table names the argument that says what a call is *doing* — a path for a file read, a
command for a shell call. Tools absent from it (every MCP tool, whose names are discovered at
runtime, plus extension and future built-in tools) fall back to a generic rendering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_MAX_VALUE_CHARS = 100
_MAX_LINE_CHARS = 160
_MAX_FALLBACK_ARGS = 3
_URL_KEYS = frozenset({"url", "uri", "endpoint"})

# Masked wherever they appear, at any nesting depth: these hold credentials rather than context.
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "env",
        "headers",
        "password",
        "secret",
        "token",
    }
)


@dataclass(frozen=True)
class ToolCallDisplay:
    """What the console needs from a ``ToolCallEvent``, decoupled from the event type."""

    phase: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class _Detail:
    """Which arguments identify a call, and whether to show them as bare values or ``key=value``."""

    keys: tuple[str, ...]
    labelled: bool = False


_DETAILS: dict[str, _Detail] = {
    "activate_skill": _Detail(("name",)),
    "apply_patch": _Detail(()),
    "bash": _Detail(("command",)),
    "calculate_expression": _Detail(("expression",)),
    "cancel_scheduled_prompt": _Detail(("job_id",)),
    "cancel_task": _Detail(("task_id",)),
    "chat_history_trim": _Detail(("keep_latest",), labelled=True),
    "code_read": _Detail(("path",)),
    "current_datetime": _Detail(()),
    "delete_scheduled_prompt": _Detail(("job_id",)),
    "fetch_agent_info": _Detail(("agent_name",)),
    "filesystem": _Detail(("action", "path", "folder"), labelled=True),
    "glob_files": _Detail(("pattern",)),
    "graph": _Detail(("action", "node", "query"), labelled=True),
    "grep": _Detail(("pattern", "path"), labelled=True),
    "http_request": _Detail(("method", "url")),
    "invoke_agent": _Detail(("agent_name",)),
    "list_skills": _Detail(("query",)),
    "memory": _Detail(("action", "title", "query"), labelled=True),
    "python_execute": _Detail(("code",)),
    "rag_delete": _Detail(("document_id",), labelled=True),
    "rag_index": _Detail(("file_path",)),
    "rag_list_metadata": _Detail(("document_id",), labelled=True),
    "rag_search": _Detail(("query",)),
    "read_file": _Detail(("path",)),
    "schedule": _Detail(("action", "job_id"), labelled=True),
    "schedule_prompt": _Detail(("run_at", "delay_seconds"), labelled=True),
    "self_insert_artifact": _Detail(("path", "filename"), labelled=True),
    "spawn_task": _Detail(("agent_name",), labelled=True),
    "transcribe_audio": _Detail(("path",)),
    "wait": _Detail(("milliseconds",), labelled=True),
}


def summarize_tool_call(call: ToolCallDisplay) -> str:
    """One transcript line for a tool call: the name, then whatever identifies this invocation."""
    detail = _detail_for(call)
    line = f"`{call.tool_name}`" + (f" — {detail}" if detail else "")
    if call.phase == "failed":
        line += f" — **failed**: {_clip(str(call.error or 'unknown error'))}"
    return _clip(line, limit=_MAX_LINE_CHARS)


def _detail_for(call: ToolCallDisplay) -> str:
    if not call.arguments:
        return ""
    spec = _DETAILS.get(call.tool_name)
    if spec is None:
        return _generic_detail(call.arguments)
    parts = [_render(key, call.arguments[key], labelled=spec.labelled) for key in spec.keys if key in call.arguments]
    return " ".join(part for part in parts if part)


def _generic_detail(arguments: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in arguments.items():
        if len(parts) >= _MAX_FALLBACK_ARGS:
            break
        if not isinstance(value, (str, int, float, bool)):
            continue
        rendered = _render(key, value, labelled=True)
        if rendered:
            parts.append(rendered)
    return " ".join(parts)


def _render(key: str, value: Any, *, labelled: bool) -> str:
    if _is_secret(key):
        return f"{key}=<redacted>"
    if isinstance(value, bool) or value is None:
        text = str(value)
    elif isinstance(value, str) and key.lower() in _URL_KEYS:
        text = _clip(_redact_url_secrets(value))
    elif isinstance(value, (str, int, float)):
        text = _clip(str(value))
    else:
        # A list or dict identifies nothing useful in one line; say only how big it is.
        text = (
            f"<{type(value).__name__} of {len(value)}>" if hasattr(value, "__len__") else f"<{type(value).__name__}>"
        )
    if not text.strip():
        return ""
    return f"{key}={text}" if labelled else text


def _redact_url_secrets(url: str) -> str:
    """Mask credential-bearing query parameters, which key-based masking alone would miss."""
    parsed = urlsplit(url)
    if not parsed.query:
        return url
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if not any(_is_secret(name) for name, _ in pairs):
        return url
    redacted = [(name, "<redacted>" if _is_secret(name) else value) for name, value in pairs]
    return urlunsplit(parsed._replace(query=urlencode(redacted, safe="<>")))


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return lowered in _SECRET_KEYS or any(secret in lowered for secret in ("token", "secret", "password", "api_key"))


def _clip(text: str, *, limit: int = _MAX_VALUE_CHARS) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}…"
