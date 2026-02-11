from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Any, Callable, Coroutine, Literal

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
        self._stdio_process: asyncio.subprocess.Process | None = None
        self._stdio_loop: asyncio.AbstractEventLoop | None = None
        self._stdio_lock: asyncio.Lock | None = None
        self._stdio_start_lock: asyncio.Lock | None = None
        self._blocking_runner = _BlockingLoopRunner()

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
            await self._ensure_stdio_process()
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
        process = await self._ensure_stdio_process()
        assert process.stdin is not None
        assert process.stdout is not None
        request_id = payload["id"]
        stdio_lock, _ = self._ensure_stdio_runtime()
        async with stdio_lock:
            process.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
            await process.stdin.drain()
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=self._timeout_seconds)
                if not line:
                    raise RuntimeError(f"empty mcp stdio response: {await self._read_stdio_stderr(process)}")
                parsed = json.loads(line.decode("utf-8"))
                if parsed.get("id") != request_id:
                    continue
                if "error" in parsed:
                    raise RuntimeError(f"mcp server error: {parsed['error']}")
                return parsed

    async def _ensure_stdio_process(self) -> asyncio.subprocess.Process:
        _, stdio_start_lock = self._ensure_stdio_runtime()
        process = self._stdio_process
        if process is not None and process.returncode is None:
            return process
        async with stdio_start_lock:
            process = self._stdio_process
            if process is not None and process.returncode is None:
                return process
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
            self._stdio_process = process
            response = await self._request_stdio_raw(
                {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "minibot", "version": "0.0.3"},
                    },
                }
            )
            if "error" in response:
                raise RuntimeError(f"mcp server error: {response['error']}")
            await self._send_stdio_notification("notifications/initialized", params={}, process=process)
            return process

    async def _send_stdio_notification(
        self, method: str, params: dict[str, Any], *, process: asyncio.subprocess.Process | None = None
    ) -> None:
        active_process = process or await self._ensure_stdio_process()
        assert active_process.stdin is not None
        stdio_lock, _ = self._ensure_stdio_runtime()
        async with stdio_lock:
            notification = {"jsonrpc": "2.0", "method": method, "params": params}
            active_process.stdin.write((json.dumps(notification, separators=(",", ":")) + "\n").encode("utf-8"))
            await active_process.stdin.drain()

    async def _request_stdio_raw(self, payload: dict[str, Any]) -> dict[str, Any]:
        process = self._stdio_process
        if process is None:
            raise RuntimeError("mcp stdio process is not started")
        assert process.stdin is not None
        assert process.stdout is not None
        request_id = payload["id"]
        stdio_lock, _ = self._ensure_stdio_runtime()
        async with stdio_lock:
            process.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
            await process.stdin.drain()
            while True:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=self._timeout_seconds)
                if not line:
                    raise RuntimeError(f"empty mcp stdio response: {await self._read_stdio_stderr(process)}")
                parsed = json.loads(line.decode("utf-8"))
                if parsed.get("id") == request_id:
                    return parsed

    def _ensure_stdio_runtime(self) -> tuple[asyncio.Lock, asyncio.Lock]:
        current_loop = asyncio.get_running_loop()
        if self._stdio_loop is current_loop and self._stdio_lock is not None and self._stdio_start_lock is not None:
            return self._stdio_lock, self._stdio_start_lock
        process = self._stdio_process
        if process is not None and process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        self._stdio_process = None
        self._stdio_loop = current_loop
        self._stdio_lock = asyncio.Lock()
        self._stdio_start_lock = asyncio.Lock()
        return self._stdio_lock, self._stdio_start_lock

    async def _read_stdio_stderr(self, process: asyncio.subprocess.Process) -> str:
        if process.stderr is None:
            return ""
        stderr_data = await process.stderr.read()
        return stderr_data.decode("utf-8", errors="ignore")

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
        return self._blocking_runner.run(self.list_tools)

    def call_tool_blocking(self, tool_name: str, payload: dict[str, Any]) -> MCPToolCallResult:
        return self._blocking_runner.run(lambda: self.call_tool(tool_name, payload))


class _BlockingLoopRunner:
    def __init__(self) -> None:
        self._lock = Lock()
        self._ready = Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Thread | None = None

    def run(self, coro_factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro_factory())

        loop = self._ensure_loop()
        future: Future[Any] = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
        return future.result()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            self._ready.clear()

            def _runner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                self._ready.set()
                loop.run_forever()

            self._thread = Thread(target=_runner, daemon=True)
            self._thread.start()

        self._ready.wait()
        if self._loop is None:
            raise RuntimeError("failed to start blocking loop runner")
        return self._loop


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
