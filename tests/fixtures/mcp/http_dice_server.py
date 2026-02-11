from __future__ import annotations

import argparse
import asyncio
import json
import random
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


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        headers_data = await reader.readuntil(b"\r\n\r\n")
        headers_text = headers_data.decode("utf-8", errors="ignore")
        content_length = 0
        for line in headers_text.split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
                break
        body = await reader.readexactly(content_length) if content_length > 0 else b"{}"
        payload = json.loads(body.decode("utf-8"))
        method = payload.get("method")
        request_id = payload.get("id")
        if method == "initialize":
            response_payload = _response(
                request_id,
                {"serverInfo": {"name": "dice-http", "version": "0.1.0"}, "capabilities": {"tools": {}}},
            )
        elif method == "tools/list":
            response_payload = _response(
                request_id,
                {
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
                },
            )
        elif method == "tools/call":
            params = payload.get("params", {})
            if params.get("name") != "roll_dice":
                response_payload = _error(request_id, "unknown tool")
            else:
                response_payload = _response(request_id, _roll(params.get("arguments", {})))
        else:
            response_payload = _error(request_id, f"unsupported method: {method}")
        encoded = json.dumps(response_payload).encode("utf-8")
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(encoded)}\r\n".encode()
            + b"Connection: close\r\n\r\n"
            + encoded
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def _run(port: int) -> None:
    server = await asyncio.start_server(_handle, "127.0.0.1", port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(_run(args.port))


if __name__ == "__main__":
    main()
