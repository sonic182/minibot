from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any, Dict

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import HTTPClientToolConfig
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.http_client import HTTPClientTool


@pytest_asyncio.fixture()
async def http_server(unused_tcp_port: int) -> AsyncGenerator[Dict[str, Any], None]:
    port = unused_tcp_port
    state: Dict[str, Any] = {"body": b"hello from server", "content_type": "text/plain"}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(65536)
        body: bytes = state["body"]
        content_type: str = state["content_type"]
        response = (
            b"HTTP/1.1 200 OK\r\n"
            + f"Content-Type: {content_type}\r\n".encode()
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + body
        )
        writer.write(response)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    await server.start_serving()
    yield {"url": f"http://127.0.0.1:{port}/", "state": state}
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_http_tool_fetches_data(http_server: Dict[str, Any]) -> None:
    config = HTTPClientToolConfig(enabled=True, timeout_seconds=5, max_bytes=1024)
    binding = HTTPClientTool(config).bindings()[0]
    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )
    assert result["status"] == 200
    assert "hello" in result["body"]
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_http_tool_truncates_large_response(http_server: Dict[str, Any]) -> None:
    http_server["state"]["body"] = b"a" * 5000
    config = HTTPClientToolConfig(enabled=True, timeout_seconds=5, max_bytes=100)
    binding = HTTPClientTool(config).bindings()[0]
    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )
    assert result["status"] == 200
    assert len(result["body"]) <= 100
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_http_tool_rejects_invalid_method(http_server: Dict[str, Any]) -> None:
    config = HTTPClientToolConfig(enabled=True, timeout_seconds=5, max_bytes=100)
    binding = HTTPClientTool(config).bindings()[0]
    with pytest.raises(ValueError):
        await binding.handler(
            {"method": "TRACE", "url": http_server["url"]},
            ToolContext(owner_id="tester"),
        )


@pytest.mark.asyncio
async def test_http_tool_auto_processes_html_and_caps_chars(http_server: Dict[str, Any]) -> None:
    http_server["state"]["content_type"] = "text/html"
    http_server["state"]["body"] = (
        b"<html><head><title>MiniBot</title><style>.x{display:none;}</style></head>"
        b"<body><h1>News</h1><p>Hello <b>world</b> from html.</p><script>ignored()</script></body></html>"
    )
    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=4096,
        response_processing_mode="auto",
        max_chars=20,
    )
    binding = HTTPClientTool(config).bindings()[0]
    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )
    assert result["status"] == 200
    assert result["processor_used"] == "html_text"
    assert result["content_type"] == "text/html"
    assert "<h1>" not in result["body"]
    assert result["truncated_chars"] is True
    assert len(result["body"]) == 20


@pytest.mark.asyncio
async def test_http_tool_auto_skips_json_processing(http_server: Dict[str, Any]) -> None:
    http_server["state"]["content_type"] = "application/json"
    http_server["state"]["body"] = b'{"message":"hello","count":2}'
    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=4096,
        response_processing_mode="auto",
        max_chars=None,
    )
    binding = HTTPClientTool(config).bindings()[0]
    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )
    assert result["status"] == 200
    assert result["processor_used"] == "none"
    assert result["content_type"] == "application/json"
    assert result["body"] == '{"message":"hello","count":2}'
