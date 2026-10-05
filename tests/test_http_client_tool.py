from __future__ import annotations

import asyncio
import gzip
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import HTTPClientToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.http_client import HTTPClientTool


@pytest_asyncio.fixture()
async def http_server(unused_tcp_port: int) -> AsyncGenerator[dict[str, Any], None]:
    port = unused_tcp_port
    state: dict[str, Any] = {"body": b"hello from server", "content_type": "text/plain"}

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
async def test_http_tool_fetches_data(http_server: dict[str, Any]) -> None:
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
async def test_http_tool_truncates_large_response(http_server: dict[str, Any]) -> None:
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
async def test_http_tool_rejects_invalid_method(http_server: dict[str, Any]) -> None:
    config = HTTPClientToolConfig(enabled=True, timeout_seconds=5, max_bytes=100)
    binding = HTTPClientTool(config).bindings()[0]
    with pytest.raises(ValueError):
        await binding.handler(
            {"method": "TRACE", "url": http_server["url"]},
            ToolContext(owner_id="tester"),
        )


@pytest.mark.asyncio
async def test_http_tool_auto_processes_html_and_caps_chars(http_server: dict[str, Any]) -> None:
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
    assert result["processor_used"] == "html_compact"
    assert result["content_type"] == "text/html"
    assert "<h1>" not in result["body"]
    assert result["truncated_chars"] is True
    assert len(result["body"]) == 20


@pytest.mark.asyncio
async def test_http_tool_auto_skips_json_processing(http_server: dict[str, Any]) -> None:
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
async def test_http_tool_spills_large_response_to_managed_file(tmp_path: Path, http_server: dict[str, Any]) -> None:
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
    assert result["processor_used"] == "html_compact"
    assert len(result["body"]) == 120
    saved = Path(str(result["body_file_absolute_path"]))
    assert saved.read_bytes() == response_body
    assert "MiniBot" in result["body"]


@pytest.mark.asyncio
async def test_http_tool_skips_spill_when_response_exceeds_max_spill_bytes(
    tmp_path: Path,
    http_server: dict[str, Any],
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
async def test_http_tool_spill_write_failure_notice_does_not_blame_max_spill_bytes(
    tmp_path: Path,
    http_server: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A storage write failure (not a size overflow) must not be reported as exceeding max_spill_bytes."""
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
        max_spill_bytes=100_000,
        spill_subdir="http_responses/tmp",
    )
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=10_000_000)

    def _raise(*_args: Any, **_kwargs: Any) -> dict[str, str | int]:
        raise OSError("disk full")

    monkeypatch.setattr(storage, "create_managed_temp_bytes_file", _raise)
    binding = HTTPClientTool(config, storage=storage).bindings()[0]

    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )

    assert result["status"] == 200
    assert result["body_storage"] == "inline"
    assert result["body_notice"] is not None
    assert "max_spill_bytes" not in result["body_notice"]
    assert "could not be saved" in result["body_notice"]


@pytest.mark.asyncio
async def test_http_tool_falls_back_to_inline_when_spill_storage_unavailable(http_server: dict[str, Any]) -> None:
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


@pytest.mark.asyncio
async def test_http_tool_compact_mode_keeps_link_targets_and_forms(http_server: dict[str, Any]) -> None:
    http_server["state"]["content_type"] = "text/html"
    http_server["state"]["body"] = (
        b'<html><body><div class="wrap"><a href="/products/123">MacBook</a>'
        b'<form action="/cart" method="post"><input type="email" name="user" placeholder="Email">'
        b"<button>Buy</button></form></div></body></html>"
    )
    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=4096,
        response_processing_mode="compact",
    )
    binding = HTTPClientTool(config).bindings()[0]
    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )

    assert result["processor_used"] == "html_compact"
    assert 'a "MacBook" /products/123' in result["body"]
    assert "form POST /cart" in result["body"]
    assert 'input email user "Email"' in result["body"]
    assert "class" not in result["body"]


@pytest.mark.asyncio
async def test_http_tool_compact_mode_falls_back_without_selectolax(
    http_server: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    http_server["state"]["content_type"] = "text/html"
    http_server["state"]["body"] = b"<html><body><h1>News</h1><p>Hello <b>world</b>.</p></body></html>"

    def _raise(_text: str) -> str:
        raise RuntimeError("HTML compaction requires selectolax")

    monkeypatch.setattr("minibot.llm.tools.http_client.html_to_compact", _raise)
    config = HTTPClientToolConfig(enabled=True, timeout_seconds=5, max_bytes=4096)
    binding = HTTPClientTool(config).bindings()[0]
    result = cast(
        dict[str, Any],
        await binding.handler(
            {"method": "GET", "url": http_server["url"]},
            ToolContext(owner_id="tester"),
        ),
    )

    # selectolax missing: the compact renderer raises and the tool degrades to plain text.
    # ``processor_used`` still reports "html_compact" (pre-existing); the body tells the truth.
    assert result["body"] == "News Hello world."
    assert 'h1 "News"' not in result["body"]


@pytest.mark.asyncio
async def test_http_tool_text_mode_keeps_legacy_plain_text(http_server: dict[str, Any]) -> None:
    http_server["state"]["content_type"] = "text/html"
    http_server["state"]["body"] = b"<html><body><h1>News</h1><p>Hello <b>world</b>.</p></body></html>"
    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=4096,
        response_processing_mode="text",
    )
    binding = HTTPClientTool(config).bindings()[0]
    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )

    assert result["processor_used"] == "html_text"
    assert result["body"] == "News Hello world."


@pytest.mark.asyncio
async def test_http_tool_none_mode_returns_raw_html(http_server: dict[str, Any]) -> None:
    body = b"<html><body><h1>News</h1></body></html>"
    http_server["state"]["content_type"] = "text/html"
    http_server["state"]["body"] = body
    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=4096,
        response_processing_mode="none",
    )
    binding = HTTPClientTool(config).bindings()[0]
    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )

    assert result["processor_used"] == "none"
    assert result["body"] == body.decode("utf-8")


@pytest.mark.asyncio
async def test_http_tool_keeps_markup_heavy_page_inline_when_it_compacts_small(
    tmp_path: Path,
    http_server: dict[str, Any],
) -> None:
    """Spill is decided on the processed size, so heavy markup that compacts small stays inline."""
    cards = "".join(
        f'<div class="card flex flex-col gap-2" data-testid="p-{index}" data-analytics="{index}00000">'
        f'<a class="font-bold hover:underline" href="/p/{index}">Item {index}</a></div>'
        for index in range(400)
    )
    response_body = f"<html><body><main>{cards}</main></body></html>".encode()
    http_server["state"]["content_type"] = "text/html"
    http_server["state"]["body"] = response_body
    spill_after_chars = 20000
    assert len(response_body) > spill_after_chars

    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=16384,
        response_processing_mode="auto",
        max_chars=None,
        spill_to_managed_file=True,
        spill_after_chars=spill_after_chars,
        spill_preview_chars=120,
        max_spill_bytes=1_000_000,
    )
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1_000_000)
    binding = HTTPClientTool(config, storage=storage).bindings()[0]

    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )

    assert result["body_storage"] == "inline"
    assert result["body_notice"] is None
    assert 'a "Item 399" /p/399' in result["body"]
    assert len(result["body"]) < len(response_body) / 3
    assert not any(tmp_path.rglob("*"))


@pytest.mark.asyncio
async def test_http_tool_still_spills_when_processed_body_is_large(
    tmp_path: Path,
    http_server: dict[str, Any],
) -> None:
    response_body = b"<html><body><p>" + (b"word " * 6000) + b"</p></body></html>"
    http_server["state"]["content_type"] = "text/html"
    http_server["state"]["body"] = response_body
    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=16384,
        response_processing_mode="auto",
        max_chars=None,
        spill_to_managed_file=True,
        spill_after_chars=16000,
        spill_preview_chars=120,
        max_spill_bytes=1_000_000,
    )
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1_000_000)
    binding = HTTPClientTool(config, storage=storage).bindings()[0]

    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )

    assert result["body_storage"] == "managed_file"
    assert len(result["body"]) == 120
    saved = Path(str(result["body_file_absolute_path"]))
    assert saved.read_bytes() == response_body


@pytest.mark.asyncio
async def test_http_tool_parses_html_beyond_max_bytes(http_server: dict[str, Any]) -> None:
    """max_bytes bounds the inline body, max_parse_bytes bounds what the serializer sees."""
    filler = "<div class='pad'>padding padding padding</div>" * 500
    response_body = f"<html><body>{filler}<a href='/deep'>Deep link</a></body></html>".encode()
    http_server["state"]["content_type"] = "text/html"
    http_server["state"]["body"] = response_body
    config = HTTPClientToolConfig(
        enabled=True,
        timeout_seconds=5,
        max_bytes=1024,
        max_parse_bytes=1_000_000,
        response_processing_mode="auto",
        max_chars=100_000,
    )
    binding = HTTPClientTool(config).bindings()[0]
    result = await binding.handler(
        {"method": "GET", "url": http_server["url"]},
        ToolContext(owner_id="tester"),
    )

    assert len(response_body) > 1024
    assert result["truncated"] is True
    assert 'a "Deep link" /deep' in result["body"]


@pytest.mark.asyncio
async def test_request_failure_reports_a_reason_the_model_can_act_on(unused_tcp_port: int) -> None:
    async def hang_up(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(65536)
        writer.close()

    server = await asyncio.start_server(hang_up, "127.0.0.1", unused_tcp_port)
    url = f"http://127.0.0.1:{unused_tcp_port}/nope"
    try:
        tool = HTTPClientTool(config=HTTPClientToolConfig(enabled=True, timeout_seconds=2))
        result = await tool._handle_request({"url": url, "method": "GET"}, ToolContext(owner_id="primary"))
    finally:
        server.close()
        await server.wait_closed()

    prefix = f"GET {url} failed: "
    assert result["ok"] is False
    assert result["error_code"] == "http_request_failed"
    assert cast(str, result["error"]).startswith(prefix)
    assert len(cast(str, result["error"])) > len(prefix)
    # marks the call so agent_runtime's repeated-failure guardrail can stop an endless retry loop
    assert result["is_repeated_failure_candidate"] is True
    assert result["failure_signature"]


@pytest.mark.asyncio
async def test_http_tool_stops_reading_a_chunked_body_at_the_limit(unused_tcp_port: int) -> None:
    sent = {"chunks": 0}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(65536)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n")
        try:
            for _ in range(200):
                writer.write(b"400\r\n" + b"a" * 1024 + b"\r\n")
                await writer.drain()
                sent["chunks"] += 1
                await asyncio.sleep(0)
            writer.write(b"0\r\n\r\n")
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", unused_tcp_port)
    await server.start_serving()
    try:
        config = HTTPClientToolConfig(enabled=True, timeout_seconds=5, max_bytes=1024, max_parse_bytes=2048)
        binding = HTTPClientTool(config).bindings()[0]
        result = await binding.handler(
            {"method": "GET", "url": f"http://127.0.0.1:{unused_tcp_port}/"},
            ToolContext(owner_id="tester"),
        )
    finally:
        server.close()
        await server.wait_closed()

    assert result["status"] == 200
    assert result["truncated"] is True
    assert len(result["body"]) <= 1024


@pytest.mark.asyncio
@pytest.mark.parametrize(("follow_redirects", "status", "body"), [(False, 302, ""), (True, 200, "landed")])
async def test_http_tool_follows_redirects_only_when_configured(
    unused_tcp_port: int, follow_redirects: bool, status: int, body: str
) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = (await reader.read(65536)).split(b"\r\n", 1)[0]
        if b" /final " in request_line:
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 6\r\n\r\nlanded")
        else:
            writer.write(b"HTTP/1.1 302 Found\r\nLocation: /final\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", unused_tcp_port)
    try:
        config = HTTPClientToolConfig(enabled=True, timeout_seconds=5, follow_redirects=follow_redirects)
        result = (
            await HTTPClientTool(config)
            .bindings()[0]
            .handler(
                {"method": "GET", "url": f"http://127.0.0.1:{unused_tcp_port}/"},
                ToolContext(owner_id="tester"),
            )
        )
    finally:
        server.close()
        await server.wait_closed()

    assert result["status"] == status
    assert result["body"] == body


@pytest.mark.asyncio
async def test_http_tool_decompresses_a_chunked_gzip_body(unused_tcp_port: int) -> None:
    payload = gzip.compress(b"hello gzip " * 50)

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(65536)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Encoding: gzip\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        for start in range(0, len(payload), 64):
            piece = payload[start : start + 64]
            writer.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
        writer.write(b"0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", unused_tcp_port)
    try:
        config = HTTPClientToolConfig(enabled=True, timeout_seconds=5, max_bytes=4096)
        result = (
            await HTTPClientTool(config)
            .bindings()[0]
            .handler(
                {
                    "method": "GET",
                    "url": f"http://127.0.0.1:{unused_tcp_port}/",
                    "headers": {"Accept-Encoding": "gzip"},
                },
                ToolContext(owner_id="tester"),
            )
        )
    finally:
        server.close()
        await server.wait_closed()

    assert result["status"] == 200
    assert result["body"].startswith("hello gzip hello gzip")
