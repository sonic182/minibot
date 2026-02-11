from __future__ import annotations

import json
import random

from mcp.server import Server

from tests.fixtures.mcp.dice_spec import DICE_INPUT_SCHEMA, DICE_TOOL_DESCRIPTION, DICE_TOOL_NAME

server = Server("dice-stdio")


@server.tool(name=DICE_TOOL_NAME, description=DICE_TOOL_DESCRIPTION, input_schema=DICE_INPUT_SCHEMA)
async def roll_dice(sides: int = 6, seed: int | None = None) -> dict[str, object]:
    value = random.Random(seed).randint(1, max(2, sides))
    return {"content": [{"type": "text", "text": json.dumps({"value": value, "sides": max(2, sides)})}]}


if __name__ == "__main__":
    server.run_stdio()
