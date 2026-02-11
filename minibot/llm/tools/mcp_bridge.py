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
        description = f"[{self._server_name}] {tool.description}".strip()
        llm_tool = Tool(name=tool_name, description=description, parameters=schema)

        async def _handler(payload: dict[str, Any], _: ToolContext) -> ToolResult:
            result = await self._client.call_tool(tool.name, payload)
            content = result.content
            if isinstance(content, list):
                content = _stringify_content_parts(content)
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


def _stringify_content_parts(content: list[Any]) -> str:
    rendered: list[str] = []
    for part in content:
        if isinstance(part, dict):
            if "text" in part:
                rendered.append(str(part.get("text", "")))
                continue
            rendered.append(json.dumps(part))
            continue
        rendered.append(str(part))
    return "\n".join(rendered)
