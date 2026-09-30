# Code analysis — 2026-09-30 (at 0.24.0)

Read-only review: bug hunt, improvements, and contrast with [`ROADMAP.md`](ROADMAP.md).
Static reading only — nothing below was reproduced at runtime, no tests were run, no code was
changed. `ruff check` and the pylint name gate both pass.

**Reviewed**: MCP client and bridge, tool executor, `bash`, `http_client`, task manager and worker,
agent runtime, turn service, dispatcher, scheduler, Telegram auth and approvals.

**Not reviewed**: `python_exec`, `file_storage` tools, RAG, graph, configurator, HTTP UI.

## Likely bugs (most severe first)

### 1. Every MCP tool call freezes the whole bot in the default mode

- [x] `[tools.mcp.servers]` defaults to `mode = "bridge"` (`minibot/adapters/config/schema.py:661`).
- [x] The bridge's async handler calls the blocking client method
  (`minibot/llm/tools/mcp_bridge.py:100`, `call_tool_blocking`).
- [x] Inside a running loop, `_BlockingLoopRunner.run` dispatches to a second thread and waits with
  `future.result()` (`minibot/adapters/mcp/client.py:373-381`). That blocks the main event loop for the
  duration of the call, up to `timeout_seconds`: Telegram polling, the scheduler and every other
  coroutine stall.
- [x] Fix: `await self._client.call_tool(...)`, as `MCPLazyToolBridge._handle_call_tool` already does
  (`mcp_bridge.py:252`).
- [ ] Side effect of the same design: the stdio process is bound to one loop, and
  `_ensure_stdio_runtime` (`client.py:228-241`) kills and respawns it whenever a different loop calls in
  (startup `asyncio.run` vs the daemon loop).

### 2. MCP errors look like successes in bridge mode

- [x] `result.is_error` is only logged (`mcp_bridge.py:109`); the returned content is
  `{"server", "tool", "result"}` with no error flag (`:113`).
- [x] Lazy mode returns `ok` and `is_error` (`:264-267`).

### 3. Stdio MCP servers can hang

- [x] stderr is a pipe (`client.py:172`) that is only read when stdout closes (`:304-308`).
- [x] A server that logs heavily fills the ~64 KB pipe buffer and blocks on write.
- [x] Fix: a background task draining stderr into the logger.

### 4. MCP HTTP transport is incomplete (`client.py:310-328`)

- [ ] `notifications/initialized` is never sent on HTTP (only stdio, `:195`).
- [ ] The HTTP status code is ignored; a non-2xx body fails as a confusing JSON decode error.
- [ ] `_parse_jsonrpc_payload` (`:405-411`) joins all SSE `data:` lines; a stream with a notification plus
  the result is invalid JSON. Responses are not matched by `id`.
- [ ] A new `aiosonic.HTTPClient()` is created per request and never closed (`:314`).
- [ ] Session expiry (404 on the session id) is not handled, so the client never re-initializes.
- [ ] No `MCP-Protocol-Version` header.
- [ ] `clientInfo.version` is hardcoded `"0.0.3"` (`:131`, `:188`), the same drift #82 fixed for
  `__version__`.
- [ ] Concurrent first calls can both initialize (no lock).

### 5. Cancelling or timing out a task leaves processes running

- [ ] The worker resets SIGTERM to `SIG_DFL` (`minibot/adapters/tasks/worker.py:87`), so
  `proc.terminate()` kills it with no cleanup.
- [ ] `bash` and `python_exec` children run with `start_new_session=True` (`bash.py:84`,
  `python_exec.py:530`), so they survive as orphans.
- [ ] Stdio MCP servers started by the worker (`worker.py:331`) are not cleaned up either.
- [ ] `proc.join` has no kill fallback (`manager.py:294`, `:387`, `:408`, `:413`); a worker that ignores
  SIGTERM hangs the reader and never frees its semaphore slot.
- [ ] Fix: a SIGTERM handler in the worker that kills child groups and MCP clients before exiting, plus
  `proc.kill()` after a grace period in the manager.

### 6. Unbounded retry loop in the agent runtime

- [x] `agent_runtime.py:303-310`: a reply with no tool call that contains `<tool_call>` appends a nudge and
  `continue`s.
- [x] There is no cap and `step` is not advanced, so `max_steps` never fires; only the timeout stops it.
- [x] The truncated-tool-call path right above it caps at 3 (`:282`).
- [x] A legitimate answer that quotes the literal tag loops the same way.

### 7. Memory grows before output limits apply

- [ ] `bash` uses `communicate()` (`minibot/shared/subprocess_utils.py:45`): `yes` buffers every byte until
  the timeout, and `max_output_bytes` is applied afterwards (`bash.py:209-227`).
- [ ] `http_request` awaits `response.content()` in full (`http_client.py:89`) before comparing with
  `max_bytes` (`:90`). It only sets connect and read timeouts (`:69-72`), so a slow-drip response can
  run indefinitely.

### 8. Approval buttons accept any member of an allowed group

- [ ] `minibot/adapters/messaging/telegram/service.py:221` authorizes the click with `is_authorized_ids`.
- [ ] With only `allowed_chat_ids` set (`authorization.py:12-31`), any member of that chat can approve a
  dangerous tool call.
- [ ] Consider requiring the requester, or `allowed_user_ids`, for approvals.

## Smaller issues

### Project-rule violations

- [x] `minibot/llm/services/tool_executor.py:245` classifies errors with `"arguments" in str(exc).lower()`,
  which breaks the output-classification rule. `parse_tool_call` should raise `ToolInputError` with an
  `error_code` instead.
- [ ] `_has_pseudo_tool_call_tag` (`agent_runtime.py:38`) is substring matching on model output. It is
  arguably format parsing, but it sits on the edge of the same rule, and bug 6 makes it worth revisiting.

### Behaviour

- [ ] **Cron runs in UTC**: `scheduler_service.py:417-419` uses a UTC base and there is no timezone setting,
  so `0 9 * * *` fires at 09:00 UTC.
- [ ] **Scheduled-prompt retries are nearly dead code**: `_handle_dispatch_failure` (`:303-319`) only runs
  when the event-bus publish raises, not when the turn fails.
- [ ] **Failed turns are saved as real replies**: `turn_service.py:255-271` stores "Sorry, I couldn't answer
  right now." in history as an assistant message; with DEBUG on, it also stores the exception text.
- [ ] **No reply on internal errors**: if `handler.handle` raises, `dispatcher._handle_message` only emits a
  `TurnFailedEvent` and the user gets nothing.
- [ ] **Hardcoded Spanish task messages**: `adapters/tasks/manager.py:334`, `:402`, `:607-610`, while the
  approval UI is English.
- [ ] **Stale process reference**: after a rate-limit retry `Task.proc` still points at the dead worker
  (`manager.py:350` rebinds only the local).
- [ ] **Unhelpful backoff**: `retry_after_seconds` is hardcoded 30 (`worker.py:481`); the provider's
  `Retry-After` is ignored.
- [ ] **Turns are strictly serial**: `Dispatcher._run` awaits each message inline, so one slow turn (or an
  approval wait of up to 90 s) queues everything behind it. Probably acceptable for a single owner, but
  worth stating in the docs.
- [ ] **Truncated-call counter never resets**: three truncations spread across a long run abort it
  (`agent_runtime.py:281-295`).
- [ ] **Lazy MCP name check**: `effective_tool_name` treats any tool ending in `__call_tool` as lazy
  (`tool_approval.py`), so an eager remote tool literally named `call_tool` is misread.
- [ ] **Logged URLs**: `http_request` logs the full URL, query string included (`http_client.py:86`).
- [ ] **Login shell**: `bash -lc` re-sources the user's profile files, which can re-export secrets even with
  `pass_parent_env = false`. Not verified against a real profile.

### Repo state

- [ ] `.gitignore` still contains conflict markers at lines 85-90 (`UU` in `git status`). The `qdrant/`
  pattern inside the conflict still applies, but the file needs resolving.

## Contrast with the roadmap

| Phase | Status in code |
|---|---|
| 0 bash env default | Done. |
| 1 vault | Done. |
| 2 skills for specialists | Done; the worker allowlist has `list_skills` and `activate_skill`. |
| 3 native skills | In progress. `reload_agents` and `get_settings` do not exist (no matches in `minibot/`). |
| 4 MCP OAuth and redaction | Not started. `tool_executor.py` only sanitizes arguments for logs; tool results reach the LLM unredacted. |
| 5 SMTP tool | Probably superseded. Its premise ("no generic approval mechanism") is out of date since 0.24.0 added `[tools.approval]`, and the docs now suggest mail-mcp. |
| 6 guardrail `credential_exposure` | Not started (no matches). |
| 7 bash hardening | Not started. `_coerce_cwd` is unchanged and `bash` has no `sandbox_mode`. Bugs 5 and 7 add to the case. |

### Gaps the roadmap does not cover

- [ ] Bugs 1-6 above (MCP correctness and event-loop blocking, task cleanup, runtime loop cap).
- [ ] Output-size ceilings for `bash` and `http_request`.
- [ ] The approval gate's authorization model (bug 8).

### Roadmap text to update

- [ ] Phase 5: note that the approval gate shipped, and decide whether a native SMTP tool is still wanted.
- [ ] Phase 3: status line still lists PR 3 (`reload_agents`) as next.
- [ ] Phase 4: the "MCP token refresh has no lock" item presumes an OAuth client that does not exist yet.

## Suggested order

1. [x] Await `call_tool` in the bridge and propagate `is_error` (bugs 1 and 2).
2. [x] Drain MCP stderr (bug 3).
3. [x] Cap the pseudo-tool-call retry (bug 6).
4. [ ] Worker SIGTERM cleanup plus a kill fallback (bug 5).
5. [ ] Streamed or capped reads for `bash` and `http_request` (bug 7).
6. [ ] MCP HTTP transport fixes (bug 4).
7. [ ] Approval authorization (bug 8), then the smaller items.
