from __future__ import annotations

from minibot.app.extensions import ExtensionContext


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tools.mcp.enabled:
        return
    from minibot.adapters.mcp.client import MCPClient
    from minibot.llm.tools.mcp_bridge import MCPToolBridge

    settings = mb.settings
    bindings = []
    for server in settings.tools.mcp.servers:
        client = MCPClient(
            server_name=server.name,
            transport=server.transport,
            timeout_seconds=settings.tools.mcp.timeout_seconds,
            command=server.command,
            args=server.args,
            env=server.env or None,
            cwd=server.cwd,
            url=server.url,
            headers=server.headers,
        )
        bridge = MCPToolBridge(
            server_name=server.name,
            client=client,
            name_prefix=settings.tools.mcp.name_prefix,
            enabled_tools=server.enabled_tools,
            disabled_tools=server.disabled_tools,
        )
        try:
            bindings.extend(bridge.build_bindings())
        except Exception as exc:  # noqa: BLE001
            mb.logger.exception("failed to load mcp tools", exc_info=exc, extra={"server": server.name})
    mb.add_tool(bindings)

