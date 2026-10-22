"""Smoke: with [tools.safety_classifier] on, a flagged call is blocked and a flagged result is withheld.

The classifier is a scripted fake that flags everything. The console has no approval buttons, so every
flagged call is denied and every flagged result withheld, as with [tools.approval]. Turn 1, the orchestrator
calls `bash` (checked before it runs): the command must not run and the model gets `tool_approval:denied`.
Turn 2, it calls `wait` (its result is checked): the model gets a `tool_output_withheld` notice instead.
Turn 3, a task worker calls `wait` the same way and reports what it got, which proves workers are checked.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from harness import answer, call, check, last_user_text, pending_tool_result, run, smoke_session

from minibot.extensions.tools import safety

MARKER = Path(tempfile.gettempdir()) / f"minibot-smoke-safety-{uuid4().hex[:8]}"
VERDICT = json.dumps({"verdict": "unsafe", "reason": "smoke classifier flags everything"})


class _FlagEverything:
    """The classifier LLM: always unsafe. Forked workers inherit the patch, but not this list."""

    def __init__(self) -> None:
        self.documents: list[str] = []

    def create_for_agent(self, _spec):
        return self

    async def generate(self, _history, user_message, **_kwargs):
        self.documents.append(user_message)
        return SimpleNamespace(payload=VERDICT)


def orchestrator(messages):
    result = pending_tool_result(messages)
    if result is not None:
        return answer(json.dumps(result))
    text = last_user_text(messages)
    if text.startswith("input"):
        return call("bash", command=f"touch {MARKER}", timeout_seconds=None, cwd=None, env=None)
    if text.startswith("output"):
        return call("wait", milliseconds=5)
    if text.startswith("task"):
        return call("spawn_task", prompt="Wait a moment, then report the wait result.")
    return answer(f"unexpected request: {text}")


def worker_policy(messages):
    result = pending_tool_result(messages)
    if result is not None:
        return answer(json.dumps({"worker_saw": result}))
    return call("wait", milliseconds=5)


async def scenario():
    classifier = _FlagEverything()
    config = {
        "tools": {
            "bash": {"enabled": True},
            "safety_classifier": {"enabled": True, "check_input": ["bash"], "check_output": ["wait"]},
        }
    }
    MARKER.unlink(missing_ok=True)
    with patch.object(safety, "LLMClientFactory", lambda _settings: classifier):
        async with smoke_session(orchestrator=orchestrator, worker_policy=worker_policy, config=config) as session:
            await session.say("input: run a command")
            blocked = json.loads(await session.reply(until=lambda text: '"error_code"' in text, timeout=30))
            await session.say("output: wait a bit")
            withheld = json.loads(await session.reply(until=lambda text: '"error_code"' in text, timeout=30))
            await session.say("task: wait in a worker")
            from_worker = json.loads(await session.reply(until=lambda text: '"worker_saw"' in text, timeout=30))

    check(not MARKER.exists(), "the flagged bash command ran")
    check(blocked.get("error_code") == "tool_approval:denied", f"bash was not denied: {blocked}")
    check(withheld.get("error_code") == "tool_output_withheld", f"the wait result was not withheld: {withheld}")
    worker_saw = from_worker["worker_saw"]
    check(worker_saw.get("error_code") == "tool_output_withheld", f"the worker saw the raw result: {worker_saw}")
    check(any('"tool": "bash"' in document for document in classifier.documents), "bash was never classified")
    return {
        "blocked": blocked["error_code"],
        "withheld": withheld["error_code"],
        "worker": worker_saw["error_code"],
        "classified_in_process": len(classifier.documents),
    }


if __name__ == "__main__":
    run(scenario)
