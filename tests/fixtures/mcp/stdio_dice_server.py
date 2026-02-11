from __future__ import annotations

import json
import random

from mcp.server.fastmcp import FastMCP

server = FastMCP("dice-stdio")


@server.tool(name="roll_dice")
def roll_dice(sides: int = 6, seed: int | None = None) -> str:
    value = random.Random(seed).randint(1, max(2, sides))
    return json.dumps({"value": value, "sides": max(2, sides)})


if __name__ == "__main__":
    server.run(transport="stdio")
