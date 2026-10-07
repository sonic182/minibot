from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from llm_async.models import Tool

from minibot.app.extensions import ExtensionContext
from minibot.app.mcp_servers import build_mcp_headers, mcp_client_kwargs, mcp_discovery_kwargs
from minibot.config.schema import MCPServerConfig
from minibot.core.tools import ToolContext
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import empty_object_schema

if TYPE_CHECKING:
    from minibot.adapters.mcp.client import MCPClient

RELOAD_TOOL_NAME = "reload_mcp"


@dataclass
class _ServerState:
    config: MCPServerConfig
    client: MCPClient
    bindings: list[ToolBinding]
    instructions: str
    error: str | None

    @property
    def tool_names(self) -> list[str]:
        return [binding.tool.name for binding in self.bindings]


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tools.mcp.enabled:
        return
    from minibot.llm.tools.mcp_bridge import build_mcp_bindings

    settings = mb.settings
    states: dict[str, _ServerState] = {}
    for server in settings.tools.mcp.servers:
        headers = _resolve_headers(mb, server)
        client = _new_client(mb, server, headers)
        try:
            bindings = build_mcp_bindings(**_discovery_args(mb, server, client))
            state = _ServerState(server, client, bindings, _server_instructions(client), None)
        except Exception as exc:  # noqa: BLE001
            mb.logger.exception("failed to load mcp tools", exc_info=exc, extra={"server": server.name})
            state = _ServerState(server, client, [], "", _error_message(exc, headers))
        finally:
            try:
                client.close_blocking()
            except Exception:  # noqa: BLE001
                mb.logger.warning("failed to close mcp discovery client", exc_info=True, extra={"server": server.name})
        states[server.name] = state
    status_rows: list[dict[str, Any]] = []
    extra_tools = [_reload_binding(mb, states, status_rows)] if settings.tools.mcp.reload else []
    _publish(mb, states, status_rows, extra_tools)
    if settings.http.enabled:
        mb.add_page("/mcp", "MCP", _build_page(status_rows), icon="plug")


def _resolve_headers(mb: ExtensionContext, server: MCPServerConfig) -> dict[str, str]:
    return build_mcp_headers(server, mb.vault.get if mb.vault is not None else None)


def _new_client(mb: ExtensionContext, server: MCPServerConfig, headers: dict[str, str]) -> MCPClient:
    from minibot.adapters.mcp.client import MCPClient

    timeout_seconds = mb.settings.tools.mcp.timeout_seconds
    return MCPClient(**mcp_client_kwargs(server, timeout_seconds=timeout_seconds, headers=headers))


def _discovery_args(mb: ExtensionContext, server: MCPServerConfig, client: MCPClient) -> dict[str, Any]:
    return mcp_discovery_kwargs(server, client=client, name_prefix=mb.settings.tools.mcp.name_prefix)


def _error_message(exc: Exception, headers: dict[str, str]) -> str:
    message = str(exc) or type(exc).__name__
    secret = headers.get("Authorization")
    return message.replace(secret, "***") if secret else message


def _publish(
    mb: ExtensionContext,
    states: dict[str, _ServerState],
    status_rows: list[dict[str, Any]],
    extra_tools: list[ToolBinding],
) -> None:
    mb.tools.clear()
    mb.prompt_fragments.clear()
    status_rows.clear()
    for state in states.values():
        mb.add_tool(state.bindings)
        if state.instructions and state.bindings:
            mb.add_prompt_fragment(
                _instructions_fragment(state.config.name, state.instructions), tool_names=state.tool_names
            )
        status_rows.append(
            {
                "name": state.config.name,
                "transport": state.config.transport,
                "mode": state.config.mode,
                "tools": state.tool_names,
                "error": state.error,
                "instructions": state.instructions,
            }
        )
    mb.add_tool(extra_tools)


def _reload_binding(
    mb: ExtensionContext, states: dict[str, _ServerState], status_rows: list[dict[str, Any]]
) -> ToolBinding:
    package = __spec__.parent if __spec__ is not None else __name__
    schema = Tool(
        name=RELOAD_TOOL_NAME,
        description=load_tool_description(RELOAD_TOOL_NAME, package=package),
        parameters=empty_object_schema(),
    )
    lock = asyncio.Lock()

    async def handler(_: dict[str, Any], _context: ToolContext) -> dict[str, Any]:
        async with lock:
            return await _reload(mb, states, status_rows, binding)

    binding = ToolBinding(tool=schema, handler=handler)
    return binding


async def _reload(
    mb: ExtensionContext,
    states: dict[str, _ServerState],
    status_rows: list[dict[str, Any]],
    reload_binding: ToolBinding,
) -> dict[str, Any]:
    from minibot.llm.tools.mcp_bridge import build_mcp_bindings_async

    if mb.refresh_tools is None:
        return {"ok": False, "error": "tool reloading is not available in this process"}
    before = {name for state in states.values() for name in state.tool_names}
    previous = dict(states)
    replaced: list[MCPClient] = []
    for name, state in list(states.items()):
        headers: dict[str, str] = {}
        client: MCPClient | None = None
        try:
            headers = _resolve_headers(mb, state.config)
            client = _new_client(mb, state.config, headers)
            bindings = await build_mcp_bindings_async(**_discovery_args(mb, state.config, client))
        except Exception as exc:  # noqa: BLE001
            mb.logger.warning("failed to reload mcp server", exc_info=True, extra={"server": name})
            state.error = _error_message(exc, headers)
            if client is not None:
                await _close(mb, client)
            continue
        replaced.append(state.client)
        states[name] = _ServerState(state.config, client, bindings, _server_instructions(client), None)
    _publish(mb, states, status_rows, [reload_binding])
    refresh_error: str | None = None
    try:
        await mb.refresh_tools()
    except Exception as exc:  # noqa: BLE001
        mb.logger.warning("failed to apply reloaded mcp tools", exc_info=True)
        refresh_error = str(exc) or type(exc).__name__
        discarded = [state.client for name, state in states.items() if state is not previous[name]]
        states.clear()
        states.update(previous)
        _publish(mb, states, status_rows, [reload_binding])
        replaced = discarded
    for old in replaced:
        await _close(mb, old)
    after = {name for state in states.values() for name in state.tool_names}
    payload: dict[str, Any] = {
        "ok": refresh_error is None and all(state.error is None for state in states.values()),
        "servers": [{"name": row["name"], "tools": row["tools"], "error": row["error"]} for row in status_rows],
        "added": sorted(after - before),
        "removed": sorted(before - after),
    }
    if refresh_error is not None:
        payload["error"] = f"the reloaded tools could not be applied: {refresh_error}"
    return payload


async def _close(mb: ExtensionContext, client: MCPClient) -> None:
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001
        mb.logger.warning("failed to close mcp client", exc_info=True)


def _server_instructions(client: MCPClient) -> str:
    """The server's own ``instructions`` from the initialize handshake.

    The spec calls them "instructions describing how to use the server", meant to improve the
    model's understanding of it -- guidance no individual tool description can carry. Cheap here:
    the client caches the metadata from the handshake ``build_mcp_bindings`` just performed.
    """
    metadata = client.server_metadata
    return (metadata.instructions or "").strip() if metadata is not None else ""


def _instructions_fragment(server_name: str, instructions: str) -> str:
    if not instructions:
        return ""
    return f"## MCP server: {server_name}\n\n{instructions}"


def _build_page(servers: list[dict[str, Any]]) -> Any:
    # Imported here so the starlette/jinja extra is only required when the server is switched on.
    from minibot.adapters.http import render

    async def _page(request: Any) -> Any:
        # Deliberately no live call: a dead stdio server would hang the request until the timeout.
        return render(request, "mcp.html", {"servers": servers})

    return _page
