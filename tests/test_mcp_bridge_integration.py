from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest

pytest.importorskip("mcp")

from minibot.adapters.config.schema import (
    CalculatorToolConfig,
    FileStorageToolConfig,
    HTTPClientToolConfig,
    KeyValueMemoryConfig,
    LLMMConfig,
    MCPServerConfig,
    MCPToolConfig,
    PythonExecToolConfig,
    ScheduledPromptsConfig,
    SchedulerConfig,
    Settings,
    TimeToolConfig,
    ToolsConfig,
)
from minibot.adapters.mcp.client import MCPClient, MCPServerMetadata, MCPToolCallResult, MCPToolDefinition
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.mcp_bridge import MCPLazyToolBridge, MCPToolBridge

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "mcp"


class _MemoryStub:
    async def append_history(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content

    async def get_history(self, session_id: str, limit: int | None = None):
        del session_id, limit
        return []

    async def count_history(self, session_id: str) -> int:
        del session_id
        return 0

    async def trim_history(self, session_id: str, keep_latest: int) -> int:
        del session_id, keep_latest
        return 0


class _LazyMCPClient:
    def __init__(
        self,
        *,
        tools: list[MCPToolDefinition],
        metadata: MCPServerMetadata | None = None,
        metadata_error: Exception | None = None,
    ) -> None:
        self.tools = tools
        self.metadata = metadata or MCPServerMetadata(name="lazy-server", instructions="Dice and counter operations.")
        self.metadata_error = metadata_error
        self.metadata_calls = 0
        self.list_tools_calls = 0
        self.tool_calls: list[tuple[str, dict[str, object]]] = []

    def get_server_metadata_blocking(self) -> MCPServerMetadata:
        self.metadata_calls += 1
        if self.metadata_error is not None:
            raise self.metadata_error
        return self.metadata

    async def list_tools(self) -> list[MCPToolDefinition]:
        self.list_tools_calls += 1
        return self.tools

    async def call_tool(self, tool_name: str, payload: dict[str, object]) -> MCPToolCallResult:
        self.tool_calls.append((tool_name, payload))
        return MCPToolCallResult(content=[{"type": "text", "text": "completed"}])


@pytest.fixture
def stdio_server_args() -> list[str]:
    return [sys.executable, str(FIXTURES_DIR / "stdio_dice_server.py")]


@pytest.fixture
def stdio_counter_server_args() -> list[str]:
    return [sys.executable, str(FIXTURES_DIR / "stdio_counter_server.py")]


@pytest.fixture
def http_server_url() -> str:
    pytest.importorskip("uvicorn")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = cast(int, sock.getsockname()[1])
    process = subprocess.Popen(
        [sys.executable, str(FIXTURES_DIR / "http_dice_server.py"), "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=os.environ.copy(),
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        if process.poll() is not None:
            break
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.1)
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        process.communicate(timeout=3)


def test_mcp_bridge_stdio_discovery_and_call(stdio_server_args: list[str]) -> None:
    client = MCPClient(
        server_name="dice_cli",
        transport="stdio",
        timeout_seconds=5,
        command=stdio_server_args[0],
        args=stdio_server_args[1:],
    )
    bridge = MCPToolBridge(server_name="dice_cli", client=client)
    bindings = bridge.build_bindings()

    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.tool.name == "mcp_dice_cli__roll_dice"
    assert binding.tool.parameters["type"] == "object"

    result = asyncio.run(binding.handler({"sides": 6, "seed": 7}, ToolContext(owner_id="tester")))
    payload = result.content["result"]
    assert isinstance(payload, str)
    parsed = json.loads(payload)
    assert parsed["sides"] == 6
    assert parsed["value"] == 3


def test_mcp_bridge_stdio_process_persists_across_blocking_calls(stdio_counter_server_args: list[str]) -> None:
    client = MCPClient(
        server_name="dice_cli",
        transport="stdio",
        timeout_seconds=5,
        command=stdio_counter_server_args[0],
        args=stdio_counter_server_args[1:],
    )
    bridge = MCPToolBridge(server_name="dice_cli", client=client)
    bindings = {binding.tool.name: binding for binding in bridge.build_bindings()}

    counter_binding = bindings["mcp_dice_cli__counter"]

    first = asyncio.run(counter_binding.handler({}, ToolContext(owner_id="tester")))
    second = asyncio.run(counter_binding.handler({}, ToolContext(owner_id="tester")))

    assert json.loads(first.content["result"])["count"] == 1
    assert json.loads(second.content["result"])["count"] == 2


def test_mcp_bridge_http_discovery_and_call(http_server_url: str) -> None:
    client = MCPClient(server_name="dice_http", transport="http", timeout_seconds=5, url=http_server_url)
    bridge = MCPToolBridge(server_name="dice_http", client=client)
    bindings = bridge.build_bindings()

    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.tool.name == "mcp_dice_http__roll_dice"

    result = asyncio.run(binding.handler({"sides": 8, "seed": 4}, ToolContext(owner_id="tester")))
    parsed = json.loads(result.content["result"])
    assert parsed["sides"] == 8
    assert parsed["value"] == 4


def test_mcp_extension_includes_dynamic_tools(stdio_server_args: list[str]) -> None:
    settings = Settings(
        llm=LLMMConfig(api_key="secret"),
        tools=ToolsConfig(
            kv_memory=KeyValueMemoryConfig(enabled=False),
            http_client=HTTPClientToolConfig(enabled=False),
            time=TimeToolConfig(enabled=False),
            calculator=CalculatorToolConfig(enabled=False),
            python_exec=PythonExecToolConfig(enabled=False),
            file_storage=FileStorageToolConfig(enabled=False),
            mcp=MCPToolConfig(
                enabled=True,
                timeout_seconds=5,
                servers=[
                    MCPServerConfig(
                        name="dice_cli",
                        transport="stdio",
                        command=stdio_server_args[0],
                        args=stdio_server_args[1:],
                    )
                ],
            ),
        ),
        scheduler=SchedulerConfig(prompts=ScheduledPromptsConfig(enabled=False)),
    )

    from minibot.extensions.integrations.mcp import register

    context = ExtensionContext(
        name="minibot.extensions.integrations.mcp",
        config={},
        settings=settings,
        event_bus=EventBus(),
        logger=logging.getLogger("test.mcp"),
    )
    register(context)
    names = {binding.tool.name for binding in context.tools}

    assert "mcp_dice_cli__roll_dice" in names


def test_mcp_extension_includes_lazy_bindings(stdio_server_args: list[str]) -> None:
    settings = Settings(
        llm=LLMMConfig(api_key="secret"),
        tools=ToolsConfig(
            kv_memory=KeyValueMemoryConfig(enabled=False),
            http_client=HTTPClientToolConfig(enabled=False),
            time=TimeToolConfig(enabled=False),
            calculator=CalculatorToolConfig(enabled=False),
            python_exec=PythonExecToolConfig(enabled=False),
            file_storage=FileStorageToolConfig(enabled=False),
            mcp=MCPToolConfig(
                enabled=True,
                timeout_seconds=5,
                servers=[
                    MCPServerConfig(
                        name="dice_cli",
                        mode="lazy",
                        transport="stdio",
                        command=stdio_server_args[0],
                        args=stdio_server_args[1:],
                    )
                ],
            ),
        ),
        scheduler=SchedulerConfig(prompts=ScheduledPromptsConfig(enabled=False)),
    )

    from minibot.extensions.integrations.mcp import register

    context = ExtensionContext(
        name="minibot.extensions.integrations.mcp",
        config={},
        settings=settings,
        event_bus=EventBus(),
        logger=logging.getLogger("test.mcp"),
    )
    register(context)
    names = {binding.tool.name for binding in context.tools}

    assert names == {"mcp_dice_cli__list_tools", "mcp_dice_cli__call_tool"}


def test_mcp_bridge_respects_tool_filters(stdio_server_args: list[str]) -> None:
    client = MCPClient(
        server_name="dice_cli",
        transport="stdio",
        timeout_seconds=5,
        command=stdio_server_args[0],
        args=stdio_server_args[1:],
    )
    bridge = MCPToolBridge(
        server_name="dice_cli",
        client=client,
        enabled_tools=["roll_dice"],
        disabled_tools=["roll_dice"],
    )

    assert bridge.build_bindings() == []


def test_mcp_client_collects_paginated_tool_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MCPClient(server_name="catalog", transport="http", timeout_seconds=5, url="http://example.invalid/mcp")
    client._initialized = True
    request_params: list[dict[str, str]] = []

    async def request(method: str, params: dict[str, str]) -> dict[str, object]:
        assert method == "tools/list"
        request_params.append(params)
        if not params:
            return {
                "result": {
                    "tools": [{"name": "first", "description": "first tool", "inputSchema": {}}],
                    "nextCursor": "page-2",
                }
            }
        return {"result": {"tools": [{"name": "second", "description": "second tool", "inputSchema": {}}]}}

    monkeypatch.setattr(client, "_request", request)

    tools = asyncio.run(client.list_tools())

    assert [tool.name for tool in tools] == ["first", "second"]
    assert request_params == [{}, {"cursor": "page-2"}]


def test_mcp_client_reads_initialization_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MCPClient(
        server_name="configured-name", transport="http", timeout_seconds=5, url="http://example.invalid/mcp"
    )

    async def request(method: str, params: dict[str, object]) -> dict[str, object]:
        assert method == "initialize"
        assert params["clientInfo"] == {"name": "minibot", "version": "0.0.3"}
        return {
            "result": {
                "serverInfo": {"name": "remote-name", "version": "1.2.3"},
                "instructions": "Use this server for dice operations.",
            }
        }

    monkeypatch.setattr(client, "_request", request)

    metadata = asyncio.run(client.get_server_metadata())

    assert metadata == MCPServerMetadata(
        name="remote-name",
        version="1.2.3",
        instructions="Use this server for dice operations.",
    )


def test_lazy_mcp_bridge_loads_catalog_on_demand_and_calls_tool() -> None:
    client = _LazyMCPClient(
        tools=[MCPToolDefinition(name="roll_dice", description="Roll a die.", input_schema={"type": "object"})]
    )
    bridge = MCPLazyToolBridge(server_name="dice", client=client)  # type: ignore[arg-type]

    bindings = {binding.tool.name: binding for binding in bridge.build_bindings()}

    assert set(bindings) == {"mcp_dice__list_tools", "mcp_dice__call_tool"}
    assert client.metadata_calls == 1
    assert client.list_tools_calls == 0
    assert "Dice and counter operations." in bindings["mcp_dice__list_tools"].tool.description

    catalog = asyncio.run(bindings["mcp_dice__list_tools"].handler({}, ToolContext(owner_id="tester")))
    result = asyncio.run(
        bindings["mcp_dice__call_tool"].handler(
            {"tool_name": "roll_dice", "arguments": {"sides": 6}}, ToolContext(owner_id="tester")
        )
    )

    assert catalog["ok"] is True
    assert catalog["tools"] == [
        {"name": "roll_dice", "description": "Roll a die.", "input_schema": {"type": "object"}}
    ]
    assert result == {
        "ok": True,
        "server": "dice",
        "tool": "roll_dice",
        "is_error": False,
        "result": "completed",
    }
    assert client.list_tools_calls == 1
    assert client.tool_calls == [("roll_dice", {"sides": 6})]


def test_lazy_mcp_bridge_auto_discovers_filters_and_refreshes_catalog() -> None:
    client = _LazyMCPClient(
        tools=[
            MCPToolDefinition(name="allowed", description="Allowed tool.", input_schema={}),
            MCPToolDefinition(name="hidden", description="Hidden tool.", input_schema={}),
        ]
    )
    bridge = MCPLazyToolBridge(
        server_name="filtered",
        client=client,  # type: ignore[arg-type]
        enabled_tools=["allowed", "hidden"],
        disabled_tools=["hidden"],
    )
    bindings = {binding.tool.name: binding for binding in bridge.build_bindings()}

    direct_result = asyncio.run(
        bindings["mcp_filtered__call_tool"].handler(
            {"tool_name": "allowed", "arguments": {}}, ToolContext(owner_id="tester")
        )
    )
    hidden_result = asyncio.run(
        bindings["mcp_filtered__call_tool"].handler(
            {"tool_name": "hidden", "arguments": {}}, ToolContext(owner_id="tester")
        )
    )
    asyncio.run(bindings["mcp_filtered__list_tools"].handler({}, ToolContext(owner_id="tester")))

    assert direct_result["ok"] is True
    assert hidden_result["error_code"] == "mcp_tool_not_available"
    assert client.list_tools_calls == 2
    assert client.tool_calls == [("allowed", {})]


def test_lazy_mcp_bridge_disables_cache_and_keeps_fallback_bindings() -> None:
    client = _LazyMCPClient(
        tools=[MCPToolDefinition(name="ping", description="Ping the server.", input_schema={})],
        metadata_error=RuntimeError("server offline"),
    )
    bridge = MCPLazyToolBridge(server_name="offline", client=client, catalog_cache_ttl_seconds=0)  # type: ignore[arg-type]
    bindings = {binding.tool.name: binding for binding in bridge.build_bindings()}

    first = asyncio.run(
        bindings["mcp_offline__call_tool"].handler({"tool_name": "ping", "arguments": {}}, ToolContext())
    )
    second = asyncio.run(
        bindings["mcp_offline__call_tool"].handler({"tool_name": "ping", "arguments": {}}, ToolContext())
    )

    assert "currently unavailable" in bindings["mcp_offline__list_tools"].tool.description
    assert first["ok"] is True
    assert second["ok"] is True
    assert client.list_tools_calls == 2


def test_lazy_mcp_bridge_reloads_an_expired_catalog() -> None:
    client = _LazyMCPClient(tools=[MCPToolDefinition(name="ping", description="Ping the server.", input_schema={})])
    bridge = MCPLazyToolBridge(server_name="expiring", client=client, catalog_cache_ttl_seconds=60)  # type: ignore[arg-type]
    bindings = {binding.tool.name: binding for binding in bridge.build_bindings()}

    asyncio.run(bindings["mcp_expiring__call_tool"].handler({"tool_name": "ping", "arguments": {}}, ToolContext()))
    bridge._catalog_loaded_at = 0.0
    asyncio.run(bindings["mcp_expiring__call_tool"].handler({"tool_name": "ping", "arguments": {}}, ToolContext()))

    assert client.list_tools_calls == 2
