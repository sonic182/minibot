from __future__ import annotations

import asyncio
import json


def worker_entry(pipe) -> None:
    asyncio.run(_worker_task(pipe))


async def _worker_task(pipe) -> None:
    async with pipe.open() as (rx, tx):
        raw = await rx.readline()
        payload = json.loads(raw)
        result = {"task_id": payload.get("task_id", ""), "text": payload.get("prompt", "")}
        tx.write(json.dumps(result).encode() + b"\n")
