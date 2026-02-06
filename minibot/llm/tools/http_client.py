from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from typing import Any

import aiosonic
from aiosonic.timeout import Timeouts
from llm_async.models import Tool

from minibot.adapters.config.schema import HTTPClientToolConfig
from minibot.llm.tools.base import ToolBinding, ToolContext

_SUPPORTED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


class HTTPClientTool:
    def __init__(self, config: HTTPClientToolConfig) -> None:
        self._config = config
        self._logger = logging.getLogger("minibot.http_tool")
        self._client = aiosonic.HTTPClient()

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=_http_tool_schema(), handler=self._handle_request),
        ]

    async def _handle_request(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        method = self._coerce_method(payload.get("method", "GET"))
        url = self._coerce_url(payload.get("url"))
        headers = self._coerce_headers(payload.get("headers"))
        body, json_payload = self._coerce_body(payload)
        timeouts = Timeouts(
            sock_connect=self._config.timeout_seconds,
            sock_read=self._config.timeout_seconds,
        )

        request_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeouts": timeouts,
        }
        if json_payload is not None:
            request_kwargs["json"] = json_payload
        elif body is not None:
            request_kwargs["data"] = body

        try:
            self._logger.info(
                "http tool request",
                extra={"method": method, "url": url, "owner_id": context.owner_id},
            )
            response = await self._client.request(url, method=method, **request_kwargs)
            content = await response.content()
            truncated = len(content) > self._config.max_bytes
            preview = content[: self._config.max_bytes]
            text_preview = _decode_preview(preview)
            content_type = _extract_content_type(response.headers)
            processed_body, processor_used = _process_response_text(
                text=text_preview,
                content_type=content_type,
                mode=self._config.response_processing_mode,
                normalize_whitespace=self._config.normalize_whitespace,
            )
            final_body, truncated_chars = _apply_char_cap(processed_body, self._config.max_chars)
            headers_subset = dict(list(response.headers.items())[:10])
            return {
                "status": response.status_code,
                "headers": headers_subset,
                "body": final_body,
                "truncated": truncated,
                "truncated_chars": truncated_chars,
                "processor_used": processor_used,
                "content_type": content_type,
            }
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("http tool request failed", exc_info=exc)
            return {"error": str(exc)}

    def _coerce_method(self, method: str | None) -> str:
        if not method:
            return "GET"
        upper = method.upper()
        if upper not in _SUPPORTED_METHODS:
            raise ValueError("unsupported method")
        return upper

    def _coerce_url(self, url: str | None) -> str:
        if not url or not isinstance(url, str):
            raise ValueError("url is required")
        normalized = url.strip()
        if not normalized.startswith("http://") and not normalized.startswith("https://"):
            raise ValueError("url must start with http:// or https://")
        return normalized

    def _coerce_headers(self, headers: Any) -> dict[str, str] | None:
        if headers is None:
            return None
        if not isinstance(headers, dict):
            raise ValueError("headers must be an object")
        sanitized: dict[str, str] = {}
        for key, value in headers.items():
            if not isinstance(key, str):
                raise ValueError("header names must be strings")
            sanitized[key] = str(value)
        return sanitized

    def _coerce_body(self, payload: dict[str, Any]) -> tuple[bytes | None, Any | None]:
        if payload.get("json") is not None and payload.get("body") is not None:
            raise ValueError("provide either json or body, not both")
        if payload.get("json") is not None:
            json_payload = payload["json"]
            if isinstance(json_payload, str):
                return None, json.loads(json_payload)
            raise ValueError("json must be a JSON string")
        body = payload.get("body")
        if body is None:
            return None, None
        if isinstance(body, str):
            return body.encode("utf-8"), None
        if isinstance(body, bytes):
            return body, None
        raise ValueError("body must be string or bytes")


def _http_tool_schema() -> Tool:
    return Tool(
        name="http_request",
        description=(
            "Fetch an HTTP or HTTPS resource using basic methods. "
            "Returns an object with status, headers, body, truncated, truncated_chars, "
            "processor_used, and content_type."
        ),
        parameters={
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "description": "HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)",
                },
                "url": {
                    "type": "string",
                    "description": "Absolute http(s) URL to request",
                },
                "headers": {
                    "type": ["object", "null"],
                    "additionalProperties": {"type": "string"},
                },
                "body": {
                    "type": ["string", "null"],
                    "description": "Optional request body (UTF-8 string)",
                },
                "json": {
                    "type": ["string", "null"],
                    "description": "Optional JSON payload encoded as a JSON string",
                },
            },
            "required": ["url", "method", "headers", "body", "json"],
            "additionalProperties": False,
        },
    )


def _decode_preview(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _extract_content_type(headers: Any) -> str:
    if headers is None:
        return ""
    raw = headers.get("Content-Type") or headers.get("content-type")
    if raw is None:
        return ""
    value = raw if isinstance(raw, str) else str(raw)
    return value.split(";", 1)[0].strip().lower()


def _process_response_text(text: str, content_type: str, mode: str, normalize_whitespace: bool) -> tuple[str, str]:
    if mode == "none":
        return text, "none"

    if _is_json_content_type(content_type):
        return text, "none"

    if _is_html_content_type(content_type):
        html_text = _html_to_text(text)
        if normalize_whitespace:
            html_text = _normalize_whitespace(html_text)
        return html_text, "html_text"

    plain_text = _normalize_whitespace(text) if normalize_whitespace else text
    return plain_text, "plain"


def _apply_char_cap(text: str, max_chars: int | None) -> tuple[str, bool]:
    if max_chars is None or len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _is_json_content_type(content_type: str) -> bool:
    return content_type == "application/json" or content_type.endswith("+json")


def _is_html_content_type(content_type: str) -> bool:
    return content_type in {"text/html", "application/xhtml+xml"}


def _html_to_text(text: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(text)
        parser.close()
        return parser.get_text()
    except Exception:  # noqa: BLE001
        return text


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.lower()
        if normalized in {"script", "style"}:
            self._skip_depth += 1
        elif normalized in {"br", "p", "div", "li", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1
        elif normalized in {"p", "div", "li", "section", "article"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)
