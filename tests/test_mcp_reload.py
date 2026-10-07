from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from minibot.adapters.config.schema import LLMMConfig, MCPServerConfig, MCPToolConfig, Settings, ToolsConfig
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionContext
from minibot.extensions.integrations.mcp import register


def _binding(name: str) -> SimpleNamespace:
    return SimpleNamespace(tool=SimpleNamespace(name=name), handler=None)


def _context(*, reload: bool = True) -> ExtensionContext:
    settings = Settings(
        llm=LLMMConfig(api_key="secret"),
        tools=ToolsConfig(
            mcp=MCPToolConfig(
                enabled=True,
                reload=reload,
                servers=[
                    MCPServerConfig(name="alpha", transport="stdio", command="/bin/true"),
                    MCPServerConfig(name="beta", transport="stdio", command="/bin/true"),
                ],
            ),
        ),
    )
    return ExtensionContext(
        name="minibot.extensions.integrations.mcp",
        config={},
        settings=settings,
        event_bus=EventBus(),
        logger=logging.getLogger("test.mcp.reload"),
        refresh_tools=AsyncMock(),
    )


def _boot_with(monkeypatch: pytest.MonkeyPatch, tools_by_server: dict[str, list[str]]) -> None:
    monkeypatch.setattr(
        "minibot.llm.tools.mcp_bridge.build_mcp_bindings",
        lambda **kwargs: [_binding(name) for name in tools_by_server[kwargs["server_name"]]],
    )


def _reload_with(monkeypatch: pytest.MonkeyPatch, tools_by_server: dict[str, list[str] | Exception]) -> None:
    async def _discover(**kwargs):
        outcome = tools_by_server[kwargs["server_name"]]
        if isinstance(outcome, Exception):
            raise outcome
        return [_binding(name) for name in outcome]

    monkeypatch.setattr("minibot.llm.tools.mcp_bridge.build_mcp_bindings_async", _discover)


def _tool_names(context: ExtensionContext) -> set[str]:
    return {binding.tool.name for binding in context.tools}


async def _call_reload(context: ExtensionContext) -> dict:
    binding = next(binding for binding in context.tools if binding.tool.name == "reload_mcp")
    return await binding.handler({}, MagicMock())


@pytest.mark.asyncio
async def test_reload_swaps_the_tools_and_refreshes_the_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    _boot_with(monkeypatch, {"alpha": ["mcp_alpha__one"], "beta": ["mcp_beta__one"]})
    context = _context()
    register(context)
    assert _tool_names(context) == {"mcp_alpha__one", "mcp_beta__one", "reload_mcp"}

    _reload_with(monkeypatch, {"alpha": ["mcp_alpha__two"], "beta": ["mcp_beta__one"]})
    result = await _call_reload(context)

    assert result["ok"] is True
    assert result["added"] == ["mcp_alpha__two"]
    assert result["removed"] == ["mcp_alpha__one"]
    assert _tool_names(context) == {"mcp_alpha__two", "mcp_beta__one", "reload_mcp"}
    context.refresh_tools.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_server_that_fails_to_reload_keeps_its_previous_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    _boot_with(monkeypatch, {"alpha": ["mcp_alpha__one"], "beta": ["mcp_beta__one"]})
    context = _context()
    register(context)

    _reload_with(monkeypatch, {"alpha": RuntimeError("connection refused"), "beta": ["mcp_beta__two"]})
    result = await _call_reload(context)

    assert result["ok"] is False
    servers = {server["name"]: server for server in result["servers"]}
    assert servers["alpha"]["error"] == "connection refused"
    assert servers["alpha"]["tools"] == ["mcp_alpha__one"]
    assert servers["beta"]["error"] is None
    assert _tool_names(context) == {"mcp_alpha__one", "mcp_beta__two", "reload_mcp"}


def test_reload_tool_is_absent_unless_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _boot_with(monkeypatch, {"alpha": ["mcp_alpha__one"], "beta": []})
    context = _context(reload=False)
    register(context)

    assert "reload_mcp" not in _tool_names(context)
