from __future__ import annotations

import argparse
import json
import random

import uvicorn
from mcp.server import Server
from mcp.server.streamable_http import create_app

from tests.fixtures.mcp.dice_spec import DICE_INPUT_SCHEMA, DICE_TOOL_DESCRIPTION, DICE_TOOL_NAME

server = Server("dice-http")


@server.tool(name=DICE_TOOL_NAME, description=DICE_TOOL_DESCRIPTION, input_schema=DICE_INPUT_SCHEMA)
async def roll_dice(sides: int = 6, seed: int | None = None) -> dict[str, object]:
    value = random.Random(seed).randint(1, max(2, sides))
    return {"content": [{"type": "text", "text": json.dumps({"value": value, "sides": max(2, sides)})}]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    app = create_app(server)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
