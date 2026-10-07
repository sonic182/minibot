from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from minibot.config.schema import MCPServerConfig


def build_mcp_headers(server: MCPServerConfig, lookup_secret: Callable[[str], str] | None) -> dict[str, str]:
    headers = dict(server.headers)
    if not server.auth_secret:
        return headers
    if lookup_secret is None:
        raise ValueError(f"mcp server {server.name!r} sets auth_secret but [vault] is not enabled")
    if any(key.lower() == "authorization" for key in headers):
        raise ValueError(
            f"mcp server {server.name!r} sets both auth_secret and an Authorization header; "
            'use one or the other (a header can carry "Bearer ${secret:NAME}")'
        )
    headers["Authorization"] = f"Bearer {lookup_secret(server.auth_secret)}"
    return headers


def secret_lookup(secrets: Mapping[str, str] | None) -> Callable[[str], str] | None:
    if secrets is None:
        return None

    def lookup(name: str) -> str:
        if name not in secrets:
            raise ValueError(f"vault has no secret named {name!r}")
        return secrets[name]

    return lookup


def mcp_client_kwargs(server: MCPServerConfig, *, timeout_seconds: int, headers: dict[str, str]) -> dict[str, Any]:
    return {
        "server_name": server.name,
        "transport": server.transport,
        "timeout_seconds": timeout_seconds,
        "command": server.command,
        "args": server.args,
        "env": server.env or None,
        "cwd": server.cwd,
        "url": server.url,
        "headers": headers,
    }


def mcp_discovery_kwargs(server: MCPServerConfig, *, client: Any, name_prefix: str) -> dict[str, Any]:
    return {
        "mode": server.mode,
        "server_name": server.name,
        "client": client,
        "name_prefix": name_prefix,
        "enabled_tools": server.enabled_tools,
        "disabled_tools": server.disabled_tools,
        "catalog_cache_ttl_seconds": server.catalog_cache_ttl_seconds,
    }
