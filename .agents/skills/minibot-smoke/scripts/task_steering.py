"""Smoke: the user changes a running sub-task's course by asking the orchestrator.

Turn 1, the user asks for a task and the orchestrator calls `spawn_task`. The worker keeps calling
`wait` until a steering message with the token reaches it. Turn 2, the user asks the orchestrator
to steer that task, and it calls `message_task`. The task must then finish early and answer from
the message, and steering it again once finished must report delivered=false.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from harness import (
    answer,
    call,
    check,
    last_user_text,
    message_texts,
    pending_tool_result,
    run,
    smoke_session,
    tool_results,
)

TOKEN = f"SMOKE-STEER-{uuid4().hex[:8]}"
MAX_STEPS = 60
BUSY_SECONDS = 1.5


def orchestrator(messages):
    result = pending_tool_result(messages)
    if result is not None:
        # Echo the tool result so the scenario can read it from the reply.
        return answer(json.dumps(result))
    text = last_user_text(messages)
    if text.startswith("start"):
        return call("spawn_task", prompt="Keep waiting until told otherwise.")
    if text.startswith("steer"):
        _, task_id, token = text.split()
        return call("message_task", task_id=task_id, message=f"Wrap up now and report token {token}.")
    return answer(f"unexpected request: {text}")


def worker_policy(messages):
    if any(TOKEN in text for text in message_texts(messages, role="user")):
        return answer(f"stopped on {TOKEN} after {tool_results(messages)} waits")
    # A different duration each step: the runtime stops a loop that repeats identical tool outputs.
    return call("wait", milliseconds=200 + tool_results(messages))


async def steer(session, task_id: str) -> dict:
    await session.say(f"steer {task_id} {TOKEN}")
    return json.loads(await session.reply(until=lambda text: '"delivered"' in text))


async def scenario():
    config = {"tasks": {"worker_max_steps": MAX_STEPS, "worker_max_tool_calls": MAX_STEPS}}
    async with smoke_session(orchestrator=orchestrator, worker_policy=worker_policy, config=config) as session:
        await session.say("start a task that waits")
        queued = json.loads(await session.reply(until=lambda text: '"task_id"' in text))
        task_id = queued["task_id"]

        # The consumer leases the row and forks the worker shortly after; let it get into its loop,
        # then retry until the task is running.
        await asyncio.sleep(BUSY_SECONDS)
        for _ in range(30):
            ack = await steer(session, task_id)
            if ack["delivered"]:
                break
            await asyncio.sleep(0.2)
        check(ack["delivered"], f"message_task never reached the running task: {ack}")

        result = await session.reply(until=lambda text: text.startswith("stopped on"), timeout=30)
        check(TOKEN in result, f"the task result does not reflect the steering message: {result!r}")
        waits = int(result.rsplit("after ", 1)[1].split()[0])
        check(1 < waits < MAX_STEPS, f"the task did not stop mid-loop: {waits} waits")

        late = await steer(session, task_id)
        check(late == {"task_id": task_id, "delivered": False, "reason": "task already done"}, f"late steer: {late}")
    return {"task_id": task_id, "waits": waits, "result": result, "late_reason": late.get("reason")}


if __name__ == "__main__":
    run(scenario)
