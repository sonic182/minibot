---
name: minibot-smoke
description: Smoke-test MiniBot end to end with scripted fake LLMs over the real stack (container, dispatcher, tools, SQLite task queue and forked workers), with no real provider. Use to confirm that a change to the orchestrator turn, task delegation or steering, task workers, the agent loop or tool execution really works, beyond unit tests. Not for writing pytest tests (minibot-testing) or judging real model behavior.
---

# MiniBot smoke tests

Run a scenario script that plays a conversation against a full MiniBot session, then report PASS or
FAIL with its summary. Scenarios live in `scripts/`; the shared harness is `scripts/harness.py`.

## Run

From the repo root (needs `poetry install --all-extras`; Linux or macOS, because workers are forked):

```bash
poetry run python .agents/skills/minibot-smoke/scripts/<scenario>.py      # add -v for INFO logs
```

The last line is `PASS <scenario>: {...}` (exit 0) or `FAIL <scenario>: <reason>` (exit 1). A run
takes a few seconds. With `-v`, look for the log lines the scenario is about.

| Scenario | Covers |
|---|---|
| `task_steering.py` | The user asks the orchestrator to change a running sub-task's course: `spawn_task`, consumer lease, forked worker, `message_task` over the pipe, steering folded into the worker loop, result back on the console, and `delivered=false` once the task is done |
| `codemode.py` | With `[tools.codemode]` on, the model is offered `run_tools_script` and the delegation tools but not the tools scripts call; one script uses `list_tools`, `describe_tool`, a nested tool call and `text()` |
| `safety.py` | With `[tools.safety_classifier]` on and a fake classifier that flags everything, a `bash` call is denied before it runs and a `wait` result is withheld, in the orchestrator and in a forked task worker (the console has no approval buttons) |

## What is real and what is fake

Real: the `AppContainer` composition, `Dispatcher`, the console channel, extension services (the
SQLite task consumer), `TaskManager`, the forked worker, the pipe protocol, `AgentRuntime`, the tool
executor and the tools. Fake: the two LLMs, each a *policy* function (one for the main agent, one for
task workers). Each session writes its own `config.toml` and databases to a temporary directory
(`smoke_config()`: console only, task queue polled every second, `wait` tool on, skills off) and
removes it on exit.

## Write a scenario

1. Copy `scripts/task_steering.py` and rewrite its module docstring to say what the scenario proves.
2. Write the policies. A policy gets the rendered messages of one provider step and returns
   `answer(text)` or `call(tool_name, **arguments)`, one tool call per step. Helpers:
   `last_user_text`, `message_texts(messages, role=...)`, `tool_results` (a count) and
   `pending_tool_result` (the parsed result this step answers, or `None` at the start of a turn).
   A useful orchestrator pattern: start a turn with a `call(...)` chosen from the user text, then
   echo `pending_tool_result` as JSON so the scenario can read it from the reply.
3. In `scenario()`, open `async with smoke_session(orchestrator=..., worker_policy=..., config=...)`;
   `config` is merged over `smoke_config()`. Talk with `await session.say(text)` and read with
   `await session.reply(until=predicate, timeout=...)`, which skips non-matching replies (status
   notices, progress) and keeps them all in `session.replies`. `session.offered_tools(step)` returns the
   tool names the orchestrator's provider was offered on a step. Assert with `check(condition, msg)`,
   return a small summary dict, and end the file with `run(scenario)`.
4. Prove the scenario can fail: in a throwaway script, import it, break the behavior it covers
   (patch the method to a no-op) and confirm it prints FAIL.

## Gotchas

- **The runtime stops a loop that repeats identical tool outputs** (`repeated identical tool
  outputs; returning fallback`). A policy that stalls with the same call must vary its arguments,
  as `task_steering.py` does with the `wait` duration.
- **A worker policy runs in the forked subprocess.** It reaches the worker only because
  `smoke_session` forces the `fork` start method and patches `worker.LLMClientFactory` before any
  task spawns; anything else a scenario patches for workers must be patched before the session too.
  A worker policy cannot write to scenario variables: report what it saw through its answer text.
- **A task is not running until the consumer leases it** (up to `poll_interval_seconds`, 1 s here).
  Retry a step that needs a running task instead of sleeping a fixed time.
- **A failed task still publishes a reply**, with its failure text (e.g. `reached a configured
  limit`). Match the content you expect, not just that a reply arrived.
