from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

from llm_async.models import Tool

from minibot.adapters.mcp.client import MCPClient, MCPServerMetadata, MCPToolDefinition
from minibot.core.agent_runtime import ToolResult
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.schema_utils import empty_object_schema, strict_object


@dataclass(frozen=True)
class MCPDynamicTool:
    binding: ToolBinding
    server_name: str
    remote_tool_name: str


class MCPToolBridge:
    """Bridge Model Context Protocol (MCP) servers to LLM tool bindings.

    Enabled by ``[tools.mcp]`` in ``config.toml``.  Each entry under
    ``[[tools.mcp.servers]]`` creates one bridge instance. Needs no extra: the MCP client is a
    self-contained JSON-RPC implementation with no third-party SDK dependency.

    Remote tools are exposed with the naming convention::

        <name_prefix>_<server_name>__<remote_tool_name>

    The default prefix is ``mcp``.

    Filtering:

    - ``enabled_tools`` — whitelist; only listed remote tool names are exposed.
    - ``disabled_tools`` — blacklist; listed names are always excluded.

    Key config options:

    - ``name_prefix`` — prefix for all bridged tool names (default: ``"mcp"``).
    - ``timeout_seconds`` — call timeout.
    - ``[[tools.mcp.servers]]`` — list of server definitions; each supports
      ``transport``, ``command``, ``args``, ``env``, ``cwd``, ``url``,
      ``headers``, ``enabled_tools``, ``disabled_tools``.
    """

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
            self._logger.info(
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
            self._logger.info(
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


class MCPLazyToolBridge:
    """Expose an MCP server through catalog and call bindings instead of eager remote tools."""

    def __init__(
        self,
        *,
        server_name: str,
        client: MCPClient,
        name_prefix: str = "mcp",
        enabled_tools: list[str] | None = None,
        disabled_tools: list[str] | None = None,
        catalog_cache_ttl_seconds: int = 60,
    ) -> None:
        self._server_name = server_name
        self._client = client
        self._name_prefix = name_prefix
        self._enabled_tools = set(enabled_tools or [])
        self._disabled_tools = set(disabled_tools or [])
        self._catalog_cache_ttl_seconds = catalog_cache_ttl_seconds
        self._catalog: list[MCPToolDefinition] | None = None
        self._catalog_loaded_at: float | None = None
        self._catalog_lock = Lock()
        self._logger = logging.getLogger("minibot.mcp.lazy_bridge")

    def build_bindings(self) -> list[ToolBinding]:
        metadata = self._load_server_metadata()
        summary = _build_server_summary(self._server_name, metadata)
        return [
            ToolBinding(
                tool=Tool(
                    name=self._tool_name("list_tools"),
                    description=(
                        f"Load the complete tool catalog for MCP server '{self._server_name}'. {summary} "
                        "Call this before selecting a remote tool."
                    ),
                    parameters=empty_object_schema(),
                ),
                handler=self._handle_list_tools,
            ),
            ToolBinding(
                tool=Tool(
                    name=self._tool_name("call_tool"),
                    description=(
                        f"Call a named tool from MCP server '{self._server_name}'. {summary} "
                        "Load the catalog first to obtain the remote tool schema."
                    ),
                    parameters=strict_object(
                        properties={
                            "tool_name": {"type": "string", "description": "Exact remote tool name."},
                            "arguments": {
                                "type": "object",
                                "description": "Arguments matching the remote tool schema.",
                                "additionalProperties": True,
                            },
                        },
                        required=["tool_name", "arguments"],
                    ),
                ),
                handler=self._handle_call_tool,
            ),
        ]

    def _load_server_metadata(self) -> MCPServerMetadata | None:
        try:
            return self._client.get_server_metadata_blocking()
        except Exception:
            self._logger.warning(
                "failed to load mcp server metadata; using configured fallback",
                exc_info=True,
                extra={"server": self._server_name},
            )
            return None

    async def _handle_list_tools(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        del payload
        try:
            tools, _from_cache = self._load_catalog(force_refresh=True)
        except Exception as exc:
            self._logger.warning(
                "failed to load mcp tool catalog",
                exc_info=True,
                extra={"server": self._server_name},
            )
            return self._error("mcp_catalog_unavailable", str(exc))
        return {
            "ok": True,
            "server": self._server_name,
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": _normalize_schema(tool.input_schema),
                }
                for tool in tools
            ],
        }

    async def _handle_call_tool(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        remote_tool_name = payload.get("tool_name")
        if not isinstance(remote_tool_name, str) or not remote_tool_name.strip():
            return self._error("invalid_tool_name", "tool_name must be a non-empty string")
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            return self._error("invalid_arguments", "arguments must be an object")
        remote_tool_name = remote_tool_name.strip()
        try:
            tools, _from_cache = self._load_catalog(force_refresh=False)
        except Exception as exc:
            self._logger.warning(
                "failed to load mcp tool catalog before call",
                exc_info=True,
                extra={"server": self._server_name, "tool": remote_tool_name},
            )
            return self._error("mcp_catalog_unavailable", str(exc), tool=remote_tool_name)
        if remote_tool_name not in {tool.name for tool in tools}:
            return self._error(
                "mcp_tool_not_available",
                f"tool '{remote_tool_name}' is not available from this server",
                tool=remote_tool_name,
            )

        sanitized_payload = _drop_none_values(arguments)
        self._logger.info(
            "executing lazy mcp tool",
            extra={
                "server": self._server_name,
                "tool": remote_tool_name,
                "argument_keys": sorted(sanitized_payload.keys()),
            },
        )
        try:
            result = self._client.call_tool_blocking(remote_tool_name, sanitized_payload)
        except Exception as exc:
            self._logger.warning(
                "lazy mcp tool call failed",
                exc_info=True,
                extra={"server": self._server_name, "tool": remote_tool_name},
            )
            return self._error("mcp_call_failed", str(exc), tool=remote_tool_name)
        content = result.content
        if isinstance(content, list):
            content = _stringify_content_parts(content)
        return {
            "ok": not result.is_error,
            "server": self._server_name,
            "tool": remote_tool_name,
            "is_error": result.is_error,
            "result": content,
        }

    def _load_catalog(self, *, force_refresh: bool) -> tuple[list[MCPToolDefinition], bool]:
        with self._catalog_lock:
            if not force_refresh and self._catalog_is_fresh():
                return self._catalog or [], True
            tools = [tool for tool in self._client.list_tools_blocking() if self._is_allowed(tool.name)]
            self._catalog = tools
            self._catalog_loaded_at = time.monotonic()
            return tools, False

    def _catalog_is_fresh(self) -> bool:
        if self._catalog is None or self._catalog_loaded_at is None or self._catalog_cache_ttl_seconds == 0:
            return False
        return time.monotonic() - self._catalog_loaded_at < self._catalog_cache_ttl_seconds

    def _is_allowed(self, remote_tool_name: str) -> bool:
        if remote_tool_name in self._disabled_tools:
            return False
        if not self._enabled_tools:
            return True
        return remote_tool_name in self._enabled_tools

    def _tool_name(self, action: str) -> str:
        return f"{self._name_prefix}_{self._server_name}__{action}"

    def _error(self, code: str, message: str, *, tool: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "server": self._server_name,
            "error_code": code,
            "error": message,
        }
        if tool is not None:
            result["tool"] = tool
        return result


def build_mcp_bindings(
    *,
    mode: str,
    server_name: str,
    client: MCPClient,
    name_prefix: str,
    enabled_tools: list[str],
    disabled_tools: list[str],
    catalog_cache_ttl_seconds: int,
) -> list[ToolBinding]:
    if mode == "lazy":
        return MCPLazyToolBridge(
            server_name=server_name,
            client=client,
            name_prefix=name_prefix,
            enabled_tools=enabled_tools,
            disabled_tools=disabled_tools,
            catalog_cache_ttl_seconds=catalog_cache_ttl_seconds,
        ).build_bindings()
    if mode == "bridge":
        return MCPToolBridge(
            server_name=server_name,
            client=client,
            name_prefix=name_prefix,
            enabled_tools=enabled_tools,
            disabled_tools=disabled_tools,
        ).build_bindings()
    raise ValueError(f"unsupported mcp mode: {mode}")


def _build_server_summary(server_name: str, metadata: MCPServerMetadata | None) -> str:
    if metadata is None:
        return f"Configured MCP server '{server_name}' is currently unavailable for metadata discovery."
    instructions = metadata.instructions
    if instructions:
        return instructions
    return f"Configured MCP server '{server_name}' (remote identity: '{metadata.name}')."


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
