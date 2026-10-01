from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class MCPToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MCPToolCallResult:
    content: Any
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class MCPServerMetadata:
    name: str
    version: str | None = None
    instructions: str | None = None


class MCPClient(Protocol):
    async def list_tools(self) -> list[MCPToolDefinition]: ...

    async def get_server_metadata(self) -> MCPServerMetadata: ...

    async def call_tool(self, tool_name: str, payload: dict[str, Any]) -> MCPToolCallResult: ...

    def list_tools_blocking(self) -> list[MCPToolDefinition]: ...

    def get_server_metadata_blocking(self) -> MCPServerMetadata: ...

    def call_tool_blocking(self, tool_name: str, payload: dict[str, Any]) -> MCPToolCallResult: ...

    async def aclose(self) -> None: ...

    def close_blocking(self) -> None: ...
