from __future__ import annotations

import json
import random
import sys
from typing import Any


def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": message}}


def _roll(arguments: dict[str, Any]) -> dict[str, Any]:
    sides = int(arguments.get("sides", 6))
    seed = arguments.get("seed")
    rng = random.Random(seed)
    value = rng.randint(1, max(2, sides))
    return {
        "content": [{"type": "text", "text": json.dumps({"value": value, "sides": max(2, sides)})}],
        "isError": False,
    }


def main() -> None:
    line = sys.stdin.readline()
    if not line:
        return
    payload = json.loads(line)
    method = payload.get("method")
    request_id = payload.get("id")
    if method == "initialize":
        result = {"serverInfo": {"name": "dice-stdio", "version": "0.1.0"}, "capabilities": {"tools": {}}}
        sys.stdout.write(json.dumps(_response(request_id, result)) + "\n")
        return
    if method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "roll_dice",
                    "description": "Roll a dice with a configurable number of sides.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "sides": {"type": "integer", "minimum": 2, "default": 6},
                            "seed": {"type": ["integer", "null"]},
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                }
            ]
        }
        sys.stdout.write(json.dumps(_response(request_id, result)) + "\n")
        return
    if method == "tools/call":
        params = payload.get("params", {})
        if params.get("name") != "roll_dice":
            sys.stdout.write(json.dumps(_error(request_id, "unknown tool")) + "\n")
            return
        sys.stdout.write(json.dumps(_response(request_id, _roll(params.get("arguments", {})))) + "\n")
        return
    sys.stdout.write(json.dumps(_error(request_id, f"unsupported method: {method}")) + "\n")


if __name__ == "__main__":
    main()
