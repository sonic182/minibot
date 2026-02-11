from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal

import aiosonic


@dataclass(frozen=True)
class MCPToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class MCPToolCallResult:
    content: Any
    is_error: bool = False


class MCPClient:
    def __init__(
        self,
        *,
        server_name: str,
        transport: Literal["stdio", "http"],
        timeout_seconds: int,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._server_name = server_name
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._command = command
        self._args = list(args or [])
        self._env = env
        self._cwd = cwd
        self._url = url
        self._headers = dict(headers or {})
        self._request_id = 0
        self._initialized = False
        self._process: asyncio.subprocess.Process | None = None

    async def list_tools(self) -> list[MCPToolDefinition]:
        await self._ensure_initialized()
        response = await self._request("tools/list", params={})
        tools_payload = response.get("result", {}).get("tools", [])
        tools: list[MCPToolDefinition] = []
        for tool_payload in tools_payload:
            if not isinstance(tool_payload, dict):
                continue
            tool_name = str(tool_payload.get("name", "")).strip()
            if not tool_name:
                continue
            tools.append(
                MCPToolDefinition(
                    name=tool_name,
                    description=str(tool_payload.get("description", "")).strip(),
                    input_schema=tool_payload.get("inputSchema") or tool_payload.get("input_schema") or {},
                )
            )
        return tools

    async def call_tool(self, tool_name: str, payload: dict[str, Any]) -> MCPToolCallResult:
        await self._ensure_initialized()
        response = await self._request("tools/call", params={"name": tool_name, "arguments": payload})
        result_payload = response.get("result", {})
        return MCPToolCallResult(
            content=result_payload.get("content", result_payload), is_error=bool(result_payload.get("isError"))
        )

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        await self._request(
            "initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "minibot", "version": "0.0.3"},
            },
        )
        self._initialized = True

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }
        if self._transport == "stdio":
            return await self._request_stdio(payload)
        return await self._request_http(payload)

    async def _request_stdio(self, payload: dict[str, Any]) -> dict[str, Any]:
        process = await self._get_or_start_stdio_process()
        assert process.stdin is not None
        assert process.stdout is not None
        request_text = json.dumps(payload, separators=(",", ":")) + "\n"
        process.stdin.write(request_text.encode("utf-8"))
        await process.stdin.drain()
        line = await asyncio.wait_for(process.stdout.readline(), timeout=self._timeout_seconds)
        if not line:
            self._process = None
            raise RuntimeError(f"empty mcp stdio response from {self._server_name}")
        parsed = json.loads(line.decode("utf-8"))
        if "error" in parsed:
            raise RuntimeError(f"mcp server error: {parsed['error']}")
        return parsed

    async def _get_or_start_stdio_process(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        if not self._command:
            raise ValueError("mcp stdio command is required")
        self._process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
        )
        return self._process

    async def _request_http(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._url:
            raise ValueError("mcp http url is required")
        client = aiosonic.HTTPClient()
        response = await asyncio.wait_for(
            client.post(
                self._url,
                headers={"Content-Type": "application/json", **self._headers},
                data=json.dumps(payload).encode("utf-8"),
            ),
            timeout=self._timeout_seconds,
        )
        body = await response.content()
        parsed = json.loads(body.decode("utf-8"))
        if "error" in parsed:
            raise RuntimeError(f"mcp server error: {parsed['error']}")
        return parsed

    def list_tools_blocking(self) -> list[MCPToolDefinition]:
        return _run_coroutine_blocking(self.list_tools())


def _run_coroutine_blocking(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("blocking MCP operations cannot run inside an active event loop")
