from __future__ import annotations


def is_mcp_tool_name(name: str) -> bool:
    return name.startswith("mcp_") and "__" in name


def extract_mcp_server(name: str) -> str | None:
    if not is_mcp_tool_name(name):
        return None
    return name[len("mcp_") :].split("__", 1)[0]
