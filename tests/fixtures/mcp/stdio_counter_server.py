from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

server = FastMCP("counter-stdio")
call_count = 0


@server.tool(name="counter")
def counter() -> str:
    global call_count
    call_count += 1
    return json.dumps({"count": call_count})


if __name__ == "__main__":
    server.run(transport="stdio")
