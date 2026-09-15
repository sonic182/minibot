from __future__ import annotations

from typing import TYPE_CHECKING, Any

from minibot.app.extensions import ExtensionContext

if TYPE_CHECKING:
    from minibot.adapters.mcp.client import MCPClient


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tools.mcp.enabled:
        return
    from minibot.adapters.mcp.client import MCPClient
    from minibot.llm.tools.mcp_bridge import build_mcp_bindings

    settings = mb.settings
    bindings = []
    # What each server actually contributed at boot. Nothing else keeps this: bridge mode throws the
    # MCPToolDefinition list away once it has built the bindings, and a failed server only ever
    # reached the log. Collecting it here costs nothing and is the only view without re-querying.
    servers: list[dict[str, Any]] = []
    for server in settings.tools.mcp.servers:
        headers = dict(server.headers)
        if server.auth_secret:
            if mb.vault is None:
                raise ValueError(f"mcp server {server.name!r} sets auth_secret but [vault] is not enabled")
            if any(key.lower() == "authorization" for key in headers):
                raise ValueError(
                    f"mcp server {server.name!r} sets both auth_secret and an Authorization header; "
                    'use one or the other (a header can carry "Bearer ${secret:NAME}")'
                )
            headers["Authorization"] = f"Bearer {mb.vault.get(server.auth_secret)}"
        client = MCPClient(
            server_name=server.name,
            transport=server.transport,
            timeout_seconds=settings.tools.mcp.timeout_seconds,
            command=server.command,
            args=server.args,
            env=server.env or None,
            cwd=server.cwd,
            url=server.url,
            headers=headers,
        )
        status: dict[str, Any] = {"name": server.name, "transport": server.transport, "mode": server.mode}
        try:
            server_bindings = build_mcp_bindings(
                mode=server.mode,
                server_name=server.name,
                client=client,
                name_prefix=settings.tools.mcp.name_prefix,
                enabled_tools=server.enabled_tools,
                disabled_tools=server.disabled_tools,
                catalog_cache_ttl_seconds=server.catalog_cache_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            mb.logger.exception("failed to load mcp tools", exc_info=exc, extra={"server": server.name})
            servers.append({**status, "tools": [], "error": str(exc) or type(exc).__name__})
            continue
        bindings.extend(server_bindings)
        tool_names = [binding.tool.name for binding in server_bindings]
        instructions = _server_instructions(client)
        if instructions and server_bindings:
            mb.add_prompt_fragment(_instructions_fragment(server.name, instructions), tool_names=tool_names)
        servers.append({**status, "tools": tool_names, "error": None, "instructions": instructions})
    mb.add_tool(bindings)
    if settings.http.enabled:
        mb.add_page("/mcp", "MCP", _build_page(servers))


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
