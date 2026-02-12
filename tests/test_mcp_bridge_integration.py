from __future__ import annotations

import asyncio
import json
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
    SchedulerConfig,
    ScheduledPromptsConfig,
    Settings,
    TimeToolConfig,
    ToolsConfig,
)
from minibot.adapters.mcp.client import MCPClient
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.factory import build_enabled_tools
from minibot.llm.tools.mcp_bridge import MCPToolBridge


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "mcp"


class _MemoryStub:
    async def append_history(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content

    async def get_history(self, session_id: str, limit: int = 32):
        del session_id, limit
        return []

    async def count_history(self, session_id: str) -> int:
        del session_id
        return 0

    async def trim_history(self, session_id: str, keep_latest: int) -> int:
        del session_id, keep_latest
        return 0


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


def test_build_enabled_tools_includes_mcp_dynamic_tools(stdio_server_args: list[str]) -> None:
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

    tools = build_enabled_tools(settings, memory=_MemoryStub(), kv_memory=None, prompt_scheduler=None, event_bus=None)
    names = {binding.tool.name for binding in tools}

    assert "mcp_dice_cli__roll_dice" in names


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
