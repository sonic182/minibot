from __future__ import annotations

DEFAULT_MCP_NAME_PREFIX = "mcp"


def is_mcp_tool_name(name: str, *, prefix: str = DEFAULT_MCP_NAME_PREFIX) -> bool:
    return name.startswith(f"{prefix}_") and "__" in name


def extract_mcp_server(name: str, *, prefix: str = DEFAULT_MCP_NAME_PREFIX) -> str | None:
    if not is_mcp_tool_name(name, prefix=prefix):
        return None
    return name[len(prefix) + 1 :].split("__", 1)[0]
