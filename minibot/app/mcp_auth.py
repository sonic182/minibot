from __future__ import annotations

from collections.abc import Callable, Mapping

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
