from __future__ import annotations

import json
import random

from mcp.server.fastmcp import FastMCP

server = FastMCP("dice-stdio")
call_count = 0


@server.tool(name="roll_dice")
def roll_dice(sides: int = 6, seed: int | None = None) -> str:
    value = random.Random(seed).randint(1, max(2, sides))
    return json.dumps({"value": value, "sides": max(2, sides)})


@server.tool(name="counter")
def counter() -> str:
    global call_count
    call_count += 1
    return json.dumps({"count": call_count})


if __name__ == "__main__":
    server.run(transport="stdio")
