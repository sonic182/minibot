from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Dict

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import HTTPClientToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
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
    bindings = HTTPClientTool(config).bindings()
    assert [binding.tool.name for binding in bindings] == ["http_request"]
    binding = bindings[0]
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


@pytest.mark.asyncio
async def test_http_tool_spills_large_response_to_managed_file(tmp_path: Path, http_server: Dict[str, Any]) -> None:
    spill_after_chars = 16000
    response_body = b"<html><body><article><h1>MiniBot</h1><p>" + (b"x" * 20050) + b"</p></article></body></html>"
    http_server["state"]["content_type"] = "text/html"
    http_server["state"]["body"] = response_body
    assert len(response_body.decode("utf-8")) > spill_after_chars
    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=100,
        response_processing_mode="auto",
        max_chars=40,
        spill_to_managed_file=True,
        spill_after_chars=spill_after_chars,
        spill_preview_chars=120,
        max_spill_bytes=100_000,
        spill_subdir="http_responses/tmp",
    )
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=10)
    binding = HTTPClientTool(config, storage=storage).bindings()[0]

    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )

    assert result["status"] == 200
    assert result["body_storage"] == "managed_file"
    assert result["body_file_path"].startswith("http_responses/tmp/")
    assert result["body_file_absolute_path"] is not None
    assert result["body_file_bytes_written"] == len(response_body)
    assert result["body_notice"] is not None
    assert "saved to managed temp file" in result["body_notice"]
    assert str(result["body_file_path"]) in result["body_notice"]
    assert "up to 120 characters" in result["body_notice"]
    assert "use body_file_path with file or grep tools" in result["body_notice"]
    assert result["processor_used"] == "html_text"
    assert len(result["body"]) == 120
    saved = Path(str(result["body_file_absolute_path"]))
    assert saved.read_bytes() == response_body
    assert "MiniBot" in result["body"]


@pytest.mark.asyncio
async def test_http_tool_skips_spill_when_response_exceeds_max_spill_bytes(
    tmp_path: Path,
    http_server: Dict[str, Any],
) -> None:
    response_body = b"a" * 500
    http_server["state"]["body"] = response_body
    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=80,
        response_processing_mode="auto",
        max_chars=40,
        spill_to_managed_file=True,
        spill_after_chars=100,
        spill_preview_chars=120,
        max_spill_bytes=300,
        spill_subdir="http_responses/tmp",
    )
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=10)
    binding = HTTPClientTool(config, storage=storage).bindings()[0]

    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )

    assert result["status"] == 200
    assert result["body_storage"] == "inline"
    assert result["body_file_path"] is None
    assert result["body_file_absolute_path"] is None
    assert result["body_file_bytes_written"] is None
    assert result["body_notice"] is not None
    assert "was not saved because it exceeds max_spill_bytes" in result["body_notice"]
    assert result["body"] == "a" * 40
    assert not any(tmp_path.rglob("*"))


@pytest.mark.asyncio
async def test_http_tool_falls_back_to_inline_when_spill_storage_unavailable(http_server: Dict[str, Any]) -> None:
    http_server["state"]["body"] = b"a" * 20050
    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=80,
        response_processing_mode="auto",
        max_chars=40,
        spill_to_managed_file=True,
        spill_after_chars=16000,
        spill_preview_chars=120,
    )
    binding = HTTPClientTool(config, storage=None).bindings()[0]

    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )

    assert result["status"] == 200
    assert result["body_storage"] == "inline"
    assert result["body_file_path"] is None
    assert result["body_file_absolute_path"] is None
    assert result["body_file_bytes_written"] is None
    assert result["body_notice"] is None
    assert len(result["body"]) == 40
    assert result["truncated"] is True
