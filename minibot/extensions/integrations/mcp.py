from __future__ import annotations

from typing import Any

from minibot.app.extensions import ExtensionContext


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tools.mcp.enabled:
        return
    from minibot.adapters.mcp.client import MCPClient
    from minibot.llm.tools.mcp_bridge import build_mcp_bindings

    settings = mb.settings
    bindings = []
    instructions: list[tuple[str, str]] = []
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
        try:
            bindings.extend(
                build_mcp_bindings(
                    mode=server.mode,
                    server_name=server.name,
                    client=client,
                    name_prefix=settings.tools.mcp.name_prefix,
                    enabled_tools=server.enabled_tools,
                    disabled_tools=server.disabled_tools,
                    catalog_cache_ttl_seconds=server.catalog_cache_ttl_seconds,
                )
            )
        except Exception as exc:  # noqa: BLE001
            mb.logger.exception("failed to load mcp tools", exc_info=exc, extra={"server": server.name})
            continue
        instructions.append((server.name, _server_instructions(client, server.name, mb)))
    mb.add_tool(bindings)
    mb.add_prompt_fragment(_instructions_fragment(instructions))


def _server_instructions(client: Any, server_name: str, mb: ExtensionContext) -> str:
    """The server's own ``instructions`` from the initialize handshake.

    The spec calls them "instructions describing how to use the server", meant to improve the
    model's understanding of it -- guidance no individual tool description can carry. Cheap here:
    the client caches the metadata from the handshake ``build_mcp_bindings`` just performed.
    """
    try:
        return (client.get_server_metadata_blocking().instructions or "").strip()
    except Exception as exc:  # noqa: BLE001
        mb.logger.warning("failed to read mcp server instructions", exc_info=exc, extra={"server": server_name})
        return ""


def _instructions_fragment(instructions: list[tuple[str, str]]) -> str:
    sections = [f"### {name}\n\n{text}" for name, text in instructions if text]
    if not sections:
        return ""
    return "## MCP servers\n\n" + "\n\n".join(sections)
