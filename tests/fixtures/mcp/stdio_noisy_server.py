from __future__ import annotations

import json
import sys

NOISE = ("x" * 1023 + "\n") * 20480

RESULTS = {
    "initialize": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "serverInfo": {"name": "noisy", "version": "1"},
    },
    "tools/list": {"tools": [{"name": "ping", "description": "ping", "inputSchema": {"type": "object"}}]},
    "tools/call": {"content": [{"type": "text", "text": "pong"}]},
}


def main() -> None:
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        sys.stderr.write(NOISE)
        sys.stderr.flush()
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": RESULTS.get(message["method"], {})}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
