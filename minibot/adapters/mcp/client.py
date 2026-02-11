from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Thread
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
        self._logger = logging.getLogger("minibot.mcp.client")
        self._request_id = 0
        self._initialized = False
        self._http_session_id: str | None = None
        self._http_client_class = aiosonic.HTTPClient

    async def list_tools(self) -> list[MCPToolDefinition]:
        await self._initialize()
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
        await self._initialize()
        response = await self._request("tools/call", params={"name": tool_name, "arguments": payload})
        result_payload = response.get("result", {})
        return MCPToolCallResult(
            content=result_payload.get("content", result_payload), is_error=bool(result_payload.get("isError"))
        )

    async def _initialize(self) -> None:
        if self._initialized:
            return
        if self._transport == "stdio":
            self._initialized = True
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
        if not self._command:
            raise ValueError("mcp stdio command is required")
        process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
            cwd=self._cwd,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        initialize_payload = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "minibot", "version": "0.0.3"},
            },
        }
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        for message in (initialize_payload, initialized_notification, payload):
            process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        request_id = payload["id"]
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=self._timeout_seconds)
            if not line:
                _, stderr_data = await process.communicate()
                stderr_text = stderr_data.decode("utf-8", errors="ignore")
                raise RuntimeError(f"empty mcp stdio response: {stderr_text}")
            parsed = json.loads(line.decode("utf-8"))
            if parsed.get("id") != request_id:
                continue
            _, stderr_data = await process.communicate()
            if process.returncode not in {0, None}:
                stderr_text = stderr_data.decode("utf-8", errors="ignore")
                raise RuntimeError(f"mcp stdio process failed: {stderr_text}")
            if "error" in parsed:
                raise RuntimeError(f"mcp server error: {parsed['error']}")
            return parsed

    async def _request_http(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._url:
            raise ValueError("mcp http url is required")
        response = await asyncio.wait_for(
            self._http_client_class().post(
                self._url,
                headers=self._build_http_headers(),
                data=json.dumps(payload).encode("utf-8"),
            ),
            timeout=self._timeout_seconds,
        )
        session_id = _extract_header_value(response, "mcp-session-id")
        if session_id:
            self._http_session_id = session_id
        body = await response.content()
        parsed = _parse_jsonrpc_payload(body.decode("utf-8"))
        if "error" in parsed:
            raise RuntimeError(f"mcp server error: {parsed['error']}")
        return parsed

    def _build_http_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._headers,
        }
        if self._http_session_id:
            headers["mcp-session-id"] = self._http_session_id
        return headers

    def list_tools_blocking(self) -> list[MCPToolDefinition]:
        return _run_coroutine_blocking(self.list_tools())

    def call_tool_blocking(self, tool_name: str, payload: dict[str, Any]) -> MCPToolCallResult:
        return _run_coroutine_blocking(self.call_tool(tool_name, payload))


def _run_coroutine_blocking(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    future: Future[Any] = Future()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(coro)
            future.set_result(result)
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)
        finally:
            loop.close()

    thread = Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    return future.result()


def _parse_jsonrpc_payload(raw_payload: str) -> dict[str, Any]:
    payload = raw_payload.strip()
    if payload.startswith("event:") or "\ndata:" in payload:
        data_lines = [line[5:].strip() for line in payload.splitlines() if line.startswith("data:")]
        if data_lines:
            payload = "\n".join(data_lines).strip()
    return json.loads(payload)


def _extract_header_value(response: Any, header_name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    if hasattr(headers, "get"):
        value = headers.get(header_name)
        if value is None:
            value = headers.get(header_name.lower())
        if value is not None:
            return str(value)
    normalized_name = header_name.lower()
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == normalized_name:
                return str(value)
        return None
    if isinstance(headers, (list, tuple)):
        for item in headers:
            if isinstance(item, tuple) and len(item) >= 2 and str(item[0]).lower() == normalized_name:
                return str(item[1])
    return None
