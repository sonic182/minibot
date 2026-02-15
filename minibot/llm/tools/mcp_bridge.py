from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from llm_async.models import Tool

from minibot.adapters.mcp.client import MCPClient, MCPToolDefinition
from minibot.core.agent_runtime import ToolResult
from minibot.llm.tools.base import ToolBinding, ToolContext


@dataclass(frozen=True)
class MCPDynamicTool:
    binding: ToolBinding
    server_name: str
    remote_tool_name: str


class MCPToolBridge:
    def __init__(
        self,
        *,
        server_name: str,
        client: MCPClient,
        name_prefix: str = "mcp",
        enabled_tools: list[str] | None = None,
        disabled_tools: list[str] | None = None,
    ) -> None:
        self._server_name = server_name
        self._client = client
        self._name_prefix = name_prefix
        self._enabled_tools = set(enabled_tools or [])
        self._disabled_tools = set(disabled_tools or [])
        self._logger = logging.getLogger("minibot.mcp.bridge")

    def build_bindings(self) -> list[ToolBinding]:
        tools = self._client.list_tools_blocking()
        bindings: list[ToolBinding] = []
        for tool in tools:
            if not self._is_allowed(tool.name):
                continue
            bindings.append(self._build_binding(tool))
        return bindings

    def _is_allowed(self, remote_tool_name: str) -> bool:
        if remote_tool_name in self._disabled_tools:
            return False
        if not self._enabled_tools:
            return True
        return remote_tool_name in self._enabled_tools

    def _build_binding(self, tool: MCPToolDefinition) -> ToolBinding:
        tool_name = self._tool_name(tool.name)
        schema = _normalize_schema(tool.input_schema)
        description = _build_tool_description(self._server_name, tool.name, tool.description)
        llm_tool = Tool(name=tool_name, description=description, parameters=schema)

        async def _handler(payload: dict[str, Any], _: ToolContext) -> ToolResult:
            sanitized_payload = _drop_none_values(payload)
            self._logger.debug(
                "executing mcp bridge tool",
                extra={
                    "server": self._server_name,
                    "tool": tool.name,
                    "argument_keys": sorted(sanitized_payload.keys()),
                },
            )
            result = self._client.call_tool_blocking(tool.name, sanitized_payload)
            content = result.content
            if isinstance(content, list):
                content = _stringify_content_parts(content)
            self._logger.debug(
                "mcp bridge tool completed",
                extra={
                    "server": self._server_name,
                    "tool": tool.name,
                    "is_error": result.is_error,
                    "result_preview": str(content)[:400],
                },
            )
            return ToolResult(content={"server": self._server_name, "tool": tool.name, "result": content})

        return ToolBinding(tool=llm_tool, handler=_handler)

    def _tool_name(self, remote_tool_name: str) -> str:
        return f"{self._name_prefix}_{self._server_name}__{remote_tool_name}"


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if not schema:
        return {"type": "object", "properties": {}, "additionalProperties": True}
    if "type" not in schema:
        return {"type": "object", **schema}
    return schema


def _build_tool_description(server_name: str, remote_tool_name: str, base_description: str) -> str:
    description = f"[{server_name}] {base_description}".strip()
    hint = _PLAYWRIGHT_TOOL_HINTS.get(remote_tool_name)
    if not hint:
        return description
    return f"{description} {hint}".strip()


def _drop_none_values(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        sanitized[key] = _drop_none_in_value(value)
    return sanitized


def _drop_none_in_value(value: Any) -> Any:
    if isinstance(value, dict):
        nested: dict[str, Any] = {}
        for nested_key, nested_value in value.items():
            if nested_value is None:
                continue
            nested[nested_key] = _drop_none_in_value(nested_value)
        return nested
    if isinstance(value, list):
        return [_drop_none_in_value(item) for item in value if item is not None]
    return value


def _stringify_content_parts(content: list[Any]) -> str:
    rendered: list[str] = []
    for part in content:
        if isinstance(part, dict):
            part_type = str(part.get("type", "")).strip().lower()
            if part_type == "image":
                mime_type = str(part.get("mimeType") or part.get("mime_type") or "image/*")
                rendered.append(f"[image payload omitted ({mime_type})]")
                continue
            if "text" in part:
                rendered.append(_truncate_text(str(part.get("text", ""))))
                continue
            rendered.append(json.dumps(_redact_large_payload_fields(part), ensure_ascii=True))
            continue
        rendered.append(_truncate_text(str(part)))
    return "\n".join(rendered)


def _truncate_text(text: str, *, max_chars: int = 12_000) -> str:
    if len(text) <= max_chars:
        return text
    remaining = len(text) - max_chars
    return f"{text[:max_chars]}\n...[truncated {remaining} chars]"


def _redact_large_payload_fields(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested_value in value.items():
            if (
                isinstance(nested_value, str)
                and key.lower() in {"data", "base64", "bytes"}
                and len(nested_value) > 256
            ):
                sanitized[key] = f"<omitted {len(nested_value)} chars>"
                continue
            sanitized[key] = _redact_large_payload_fields(nested_value)
        return sanitized
    if isinstance(value, list):
        return [_redact_large_payload_fields(item) for item in value]
    if isinstance(value, str):
        return _truncate_text(value)
    return value


_PLAYWRIGHT_TOOL_HINTS: dict[str, str] = {
    "browser_take_screenshot": (
        "For normal page captures, call with type='png' and fullPage=true. "
        "Do not pass null for optional fields; omit element/ref/filename when unused."
    ),
    "browser_snapshot": (
        "filename is optional. If you do not need a file, omit filename instead of sending null/empty values."
    ),
    "browser_run_code": (
        "Use only for short, bounded scripts. Return small structured outputs and avoid base64/file contents."
    ),
}
