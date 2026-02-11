from __future__ import annotations

import argparse
import json
import random

from mcp.server.fastmcp import FastMCP

server = FastMCP("dice-http")


@server.tool(name="roll_dice")
def roll_dice(sides: int = 6, seed: int | None = None) -> str:
    value = random.Random(seed).randint(1, max(2, sides))
    return json.dumps({"value": value, "sides": max(2, sides)})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server.settings.port = args.port
    server.settings.host = "127.0.0.1"
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
