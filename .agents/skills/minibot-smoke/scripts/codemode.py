"""Smoke: with codemode on, the orchestrator uses its tools only through run_tools_script.

The model is offered run_tools_script and the tools scripts cannot call (the delegation tools), not `wait`.
One script discovers `wait` with list_tools/describe_tool, calls it, and cuts a result with text(); the
orchestrator echoes the script's result so the scenario can check it from the reply.
"""

from __future__ import annotations

import json

from harness import answer, call, check, last_user_text, pending_tool_result, run, smoke_session

SCRIPT = """
names = [entry["name"] for entry in list_tools()]
signature = describe_tool("wait")["signature"]
waited = await tools.wait(milliseconds=10)
{"names": names, "signature": signature, "waited": text(waited, limit=200), "cut": text(list(range(100)), limit=5, unit="lines")}
"""


def orchestrator(messages):
    result = pending_tool_result(messages)
    if result is not None:
        return answer(json.dumps(result))
    if last_user_text(messages).startswith("script"):
        return call("run_tools_script", code=SCRIPT)
    return answer("unexpected request")


async def scenario():
    config = {"tools": {"codemode": {"enabled": True}}}
    async with smoke_session(orchestrator=orchestrator, config=config) as session:
        await session.say("script: wait a bit")
        reply = json.loads(await session.reply(until=lambda text: '"ok"' in text, timeout=30))
        offered = session.offered_tools(0)

    check("run_tools_script" in offered, f"run_tools_script was not offered: {offered}")
    check("wait" not in offered, f"wait is still a direct tool next to run_tools_script: {offered}")
    check("spawn_task" in offered, f"the delegation tools must stay direct: {offered}")
    check(reply.get("ok") is True, f"the script failed: {reply}")
    value = reply["result"]
    check("wait" in value["names"], f"list_tools() does not list wait: {value['names']}")
    check(value["signature"].startswith("await tools.wait(*, milliseconds: int"), value["signature"])
    check(value["cut"].endswith("more lines]"), f"text() did not cut by lines: {value['cut']!r}")
    return {"offered": sorted(offered), "script_tools": value["names"], "waited": value["waited"]}


if __name__ == "__main__":
    run(scenario)
