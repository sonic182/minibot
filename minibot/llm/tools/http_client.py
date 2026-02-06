from __future__ import annotations

import json
import logging
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
            headers_subset = dict(list(response.headers.items())[:10])
            return {
                "status": response.status_code,
                "headers": headers_subset,
                "body": text_preview,
                "truncated": truncated,
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
        description="Fetch an HTTP or HTTPS resource using basic methods.",
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
