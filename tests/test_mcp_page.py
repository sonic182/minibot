from __future__ import annotations

import logging
from types import SimpleNamespace

import aiosonic
import pytest
import pytest_asyncio

from minibot.adapters.config.schema import (
    HTTPServerConfig,
    KeyValueMemoryConfig,
    LLMMConfig,
    MCPServerConfig,
    MCPToolConfig,
    ScheduledPromptsConfig,
    SchedulerConfig,
    Settings,
    ToolsConfig,
)
from minibot.adapters.http import HttpServer, set_nav_entries
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionContext
from minibot.extensions.integrations.mcp import register

TOKEN = "s3cret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _context() -> ExtensionContext:
    settings = Settings(
        llm=LLMMConfig(api_key="secret"),
        http=HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, auth_token=TOKEN),
        tools=ToolsConfig(
            kv_memory=KeyValueMemoryConfig(enabled=False),
            mcp=MCPToolConfig(
                enabled=True,
                servers=[MCPServerConfig(name="dice", transport="stdio", command="/bin/true")],
            ),
        ),
        scheduler=SchedulerConfig(prompts=ScheduledPromptsConfig(enabled=False)),
    )
    return ExtensionContext(
        name="minibot.extensions.integrations.mcp",
        config={},
        settings=settings,
        event_bus=EventBus(),
        logger=logging.getLogger("test.mcp.page"),
    )


def _fake_binding(name: str) -> SimpleNamespace:
    return SimpleNamespace(tool=SimpleNamespace(name=name), handler=None)


@pytest_asyncio.fixture()
async def serve():
    """Serve whatever routes a freshly registered extension produced."""
    started: list[HttpServer] = []

    async def _serve(context: ExtensionContext) -> HttpServer:
        set_nav_entries(list(context.pages))
        instance = HttpServer(context.settings.http, list(context.routes))
        await instance.start()
        started.append(instance)
        return instance

    try:
        yield _serve
    finally:
        for instance in started:
            await instance.stop()


@pytest.mark.asyncio
async def test_lists_each_server_with_its_tools(monkeypatch, serve) -> None:
    monkeypatch.setattr(
        "minibot.llm.tools.mcp_bridge.build_mcp_bindings",
        lambda **kwargs: [_fake_binding("mcp_dice__roll"), _fake_binding("mcp_dice__flip")],
    )
    context = _context()
    register(context)
    assert ("/mcp", "MCP", "plug") in context.pages

    server = await serve(context)
    async with aiosonic.HTTPClient() as client:
        response = await client.get(f"http://127.0.0.1:{server.port}/mcp", headers=AUTH)
        assert response.status_code == 200
        body = await response.text()

    assert "dice" in body
    assert "mcp_dice__roll" in body
    assert "mcp_dice__flip" in body
    assert "2 tools" in body


@pytest.mark.asyncio
async def test_a_server_that_failed_to_load_is_shown_not_hidden(monkeypatch, serve) -> None:
    def _boom(**kwargs):
        raise RuntimeError("stdio command not found")

    monkeypatch.setattr("minibot.llm.tools.mcp_bridge.build_mcp_bindings", _boom)
    context = _context()
    register(context)
    # A failing server must not take the whole extension down, and must contribute no tools.
    assert context.tools == []

    server = await serve(context)
    async with aiosonic.HTTPClient() as client:
        body = await (await client.get(f"http://127.0.0.1:{server.port}/mcp", headers=AUTH)).text()

    assert "failed to load" in body
    assert "stdio command not found" in body


@pytest.mark.asyncio
async def test_the_page_requires_auth(monkeypatch, serve) -> None:
    monkeypatch.setattr("minibot.llm.tools.mcp_bridge.build_mcp_bindings", lambda **kwargs: [])
    context = _context()
    register(context)

    server = await serve(context)
    async with aiosonic.HTTPClient() as client:
        assert (await client.get(f"http://127.0.0.1:{server.port}/mcp")).status_code == 401
