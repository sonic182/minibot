# Self-evolution behind an opt-in (`[evolution]`)

Status: design only, no code. Phase 14 (priority 7) in `todos/ROADMAP.md`.
Line numbers were checked at commit `8aa85bf` (HEAD of this worktree).

Findings that change the brief:

- There is no `entrypoint.sh` and no active Docker `HEALTHCHECK`. The only healthcheck in `docker-compose.yml` is a commented example for an optional RabbitMQ service (`docker-compose.yml:7-12`). The image runs `CMD ["minibot"]` (`Dockerfile:75`) with `restart: unless-stopped` (`docker-compose.yml:24`).
- `/health` exists only when `[http] enabled` is true (default false, `minibot/config/schema.py:1188`). It returns a static `{"status": "ok"}` (`minibot/adapters/http/server.py:49-50`), so it is a liveness check, not a readiness check.
- Phase 13 (codemode) is not on HEAD. It is planned in commit `b92ebd9` on branch `docs/roadmap-codemode`. Section 11 covers the interaction.
- Pending-turn replay does not cover every restart. Section 5 lists three pre-existing gaps that must be fixed before a restart can be trusted.

## 1. Problem, goals, non-goals

Problem. The owner wants MiniBot to add its own tools and services. Today that means someone edits `[extensions] modules`, writes Python by hand, and restarts the daemon.

Goals for v1, all behind `[evolution] enabled = true`:

- The model can author a standalone extension in a dedicated workspace.
- The model can validate it in a throwaway subprocess.
- The model can request activation. Activation needs an owner approval of the exact bytes approved.
- The daemon restarts to load the approved set, at a safe moment.
- The daemon can roll back, and it does not boot-loop on a bad set.
- With the switch off, nothing new is exposed: no tools, no `sys.path` change, no state read.

Non-goals for v1:

- Activation without an owner approval. The model cannot activate on its own.
- Editing MiniBot's source, `config.toml`, or the agent ceiling.
- Installing packages (no `pip`, no new dependencies).
- In-process hot reload of extension modules.
- Skills and prompt fragments. Both are v2 (section 12). Specialists are already covered by `[orchestration.agent_management]` (`minibot/app/managed_agent_policy.py:60`), so this design does not duplicate them.
- Sandboxing extension code. Python in-process code cannot be sandboxed by MiniBot (section 8).

## 2. What exists today (verified)

- Extensions load once, in `AppContainer.configure` (`minibot/adapters/container/app_container.py:92`). The loader is `load_extensions` (`minibot/app/extensions.py:316`). It imports bundled modules, then `[extensions] modules` (`extensions.py:333`). Any import or `register` failure is fatal (`extensions.py:336-356`). Every extension receives the unlocked vault (`extensions.py:101`, set at `:341-351`).
- The worker calls `load_extensions` again for every task, after reloading settings (`minibot/app/tasks/worker.py:225`). So a new module reaches workers on the next task, while the daemon still runs the old set. This must be handled (section 5).
- The registry is started once (`minibot/app/daemon.py:81-83`). Services, subscriptions and HTTP routes are built at boot (`daemon.py:95-191`). `Dispatcher.refresh_tools` only rebuilds tools from the same extension list (`minibot/app/dispatcher.py:255`). Hot reload of a module would need all of that rebuilt.
- Tool approval wraps tools whose names match `[tools.approval] require_approval` (`minibot/app/tool_factory.py:70-77`, `minibot/app/tool_approval.py:33-50`). The question goes to the chat that started the call (`tool_approval.py:83-94`). A channel without approval buttons denies (`tool_approval.py:96`). The console does not support approval (`minibot/core/channels.py:9`). A `detail` string is capped at 3000 characters (`tool_approval.py:22`, `:178`).
- Agent management is the model-authored precedent. Writes go through `AgentManagementService._persist` and `_authorize` (`minibot/app/agent_management.py:110`, `:151`). The owner ceiling is `ManagedAgentPolicy.authorize` (`minibot/app/managed_agent_policy.py:60`). The store writes through a temp file and `os.replace` (`minibot/adapters/agents/managed_store.py:51`), with a 64 KB cap (`:10`).
- Skills already have a runtime write path, `[tools.skills] write_path`. A skill written there is found without a restart (`minibot/config/schema.py:848` docstring).
- Replay exists: `PendingTurnStore` marks a turn before it starts (`minibot/app/dispatcher.py:462`) and `_replay_pending_turns` republishes surviving rows at boot (`minibot/app/daemon.py:194-211`, called at `:90`). The row is cleared in a `finally` (`dispatcher.py:574`, `:576`).
- Task workers are `multiprocessing.Process` children (`minibot/app/tasks/manager.py:328`). SQLite task leases last 2100 s by default (`minibot/config/schema.py:1041`).
- `bash` has no filesystem jail (`docs/security.rst`, trust section). `python_exec` is enabled by default with `sandbox_mode = "basic"`, which means same process user and no isolation (`minibot/config/schema.py:720`, `:724`; `minibot/llm/tools/python_exec.py:41`).
- Docker mounts `./config.toml` read-only (`docker-compose.yml:34`), `./data` read-write (`:37`), and the source `./minibot` read-write (`:38`). The image installs `git` (`Dockerfile:22`).

## 3. Configuration: `[evolution]`

New `EvolutionConfig` in `minibot/config/schema.py`, added to `Settings` next to `extensions` (`schema.py:1256`). It uses `extra="forbid"` like the other gated sections.

| Option | Default | Why |
|---|---|---|
| `enabled` | `false` | Master switch. Off means: no evolution tools, no workspace read, no `sys.path` change, no boot markers. |
| `directory` | `"./data/evolution"` | Workspace root. Under `./data` so it persists in Docker (`docker-compose.yml:37`), and not under the source mount (`:38`). |
| `max_modules` | `8` | Bounds how much model-authored code is loaded at once. |
| `max_module_bytes` | `64000` | Same ceiling as managed agent definitions (`managed_store.py:10`). |
| `activations_per_day` | `5` | Limits approval prompts and churn. Counted from `audit.jsonl`. |
| `restarts_per_hour` | `3` | Caps restart storms even when each activation was approved. |
| `max_failed_boots` | `2` | Unhealthy boots of one set before falling back to the promoted set. |
| `healthy_after_seconds` | `60` | How long the daemon must stay up after boot before a set is promoted. |
| `worker_access` | `false` | Whether evolved tools are granted to generic task workers. Today every user extension tool is granted (`worker.py:234-235`, `:472`). Evolved code should not inherit that by default. |
| `accept_host_write_tools` | `false` | Explicit acknowledgement that `bash`, `python_exec` (not `jail`) or `apply_patch` can bypass the gate (section 8). |

Validation rules, in the `Settings` model validator next to `_validate_specialist_activation` (`schema.py:1278`). When `enabled` is false, only the types are checked.

- Approval needs a channel. Require `[channels.telegram] enabled` or `[http] enabled`. A console-only daemon would deny every activation (`tool_approval.py:96`), so refuse to start in that state.
- The workspace must not overlap, by equality, containment or ancestry, any of: the installed `minibot` package directory, `[orchestration].directory`, `[orchestration.agent_management].directory`, `[tools.file_storage].root_dir` (when enabled), `[tools.apply_patch].workspace_root` (when enabled). The model's own file tools must not reach the workspace.
- `accept_host_write_tools = false` refuses to start while `[tools.bash] enabled`, `[tools.apply_patch] enabled`, or `[tools.python_exec] enabled` with a `sandbox_mode` other than `jail` is true.
- `worker_access = true` requires `[tasks] enabled`.
- `[extensions] modules` may not name a module that starts with `evo_` (the evolution prefix). This is checked at load.

## 4. Capabilities in v1

Registered by a bundled extension, `minibot/extensions/tools/evolution.py`. It mirrors `minibot/extensions/tools/agent_management.py:23-24` and returns early unless `entrypoint == "daemon"`. The console cannot approve, so it gets no evolution tools. The worker gets none either.

| Tool | Gate | Effect |
|---|---|---|
| `evolution_status` | none, read-only | Enabled, kill-switch state, candidate / promoted / loaded module names and hashes, failure count, restarts and activations used. |
| `evolution_write_module` | none | Writes `candidates/evo_<name>.py` through a temp file and `os.replace`. Confined to the workspace, size and count capped. No runtime effect. |
| `evolution_validate` | none | Runs the validator subprocess (below) on one candidate. Returns a structured report and records the hash. No runtime effect. |
| `evolution_activate` | internal approval, always | Sets the pending set to `promoted + {name}` (or removes `name`). Requests a restart after idle. |
| `evolution_rollback` | none, rate-limited | Sets the pending set to `promoted`, requests a restart. Removal only. |

Why authoring has no gate: nothing runs until activation, so gating authoring only creates approval fatigue. Why rollback has no gate: it only removes code, and it is still bounded by `restarts_per_hour` and audited.

Validation (`evolution_validate`). A subprocess runs `python -I -m minibot.adapters.evolution.check <name>` with `cwd` set to the workspace, a cleared environment (an allowlist of `PATH`, `LANG`), and a 20 s timeout. The check does the following:

1. Imports the candidate from `candidates/` with the workspace path only.
2. Builds a real `ExtensionContext` from current settings, with `evolved=True`, `vault=None`, and no agent-management or refresh callbacks.
3. Calls `register(mb)`.
4. Runs the same unique-tool-name check as `_ensure_unique_tool_names` (`minibot/app/tool_factory.py:101-109`), against the bundled tool names.
5. Prints one JSON object: `import_ok`, `register_ok`, tool names, subscription and service counts, route count, collisions, errors.

The parent parses that JSON. It does not parse text. Optional: run `ruff check` on the file if `ruff` is importable (it is a dev dependency only, `pyproject.toml:101`). This is a fail-fast gate, not a sandbox: import-time code runs in the subprocess.

Activation flow (`evolution_activate`):

1. Check the kill switch, the rate limit (`activations_per_day`), and the module limit.
2. Require a passing validation for the current candidate hash.
3. Send the full source to the owner with `send_file` (`minibot/llm/tools/file_storage.py:46`), so the owner can read it all. The approval text is capped at 3000 characters, so it cannot carry the source.
4. Call `request_tool_approval` (`minibot/app/tool_approval.py:83`) with a `detail` containing the name, sha256, size, declared tools, imported top-level modules, and a preview. The tool calls this itself, so the gate does not depend on `[tools.approval]`.
5. On approval, re-hash the candidate and refuse if the hash changed since validation. This is the time-of-check/time-of-use fix.
6. Write the pending set, with the approved hashes, to `state.json`. Append to `audit.jsonl`. Request a restart (section 6).

The approval is also required for every later call to an evolved tool: `build_enabled_tools` adds evolved tool names to the approval patterns (`tool_factory.py:70-77`). The console denies (`tool_approval.py:96`).

## 5. Restart design

### 5.1 In-process reload: rejected

Reloading a module in the running process would mean rebuilding the registry, the started services, subscriptions, HTTP routes, prompt fragments and the dispatcher tool list (`daemon.py:81-191`, `extensions.py:278-300`, `dispatcher.py:255`), plus `sys.modules` cleanup. That is a larger change than the feature, and the failure modes are worse than a clean process exit.

### 5.2 Process restart: chosen

The daemon restarts itself by exiting with code 75 (`EX_TEMPFAIL`) after a graceful stop. The steps:

1. `evolution_activate` or `evolution_rollback` writes `restart_requested` to `state.json`.
2. A coordinator waits until the daemon is idle. Idle means: no active turn (`dispatcher.py:116`), an empty backlog (`dispatcher.py:114`, which is in memory only), no task rows in `leased` or `running` state, and no unflushed outbound reply. The check is a new `Dispatcher.is_idle()`. It polls every second and gives up after about 10 minutes. If it gives up, the restart stays pending and the owner is told.
3. The coordinator runs `_graceful_shutdown` (`minibot/app/daemon.py:215-233`). Then `daemon.main` (`daemon.py:247-271`) exits with 75.

Supervisor contract:

- Docker: `restart: unless-stopped` (`docker-compose.yml:24`) restarts the container on any exit code. Exit 75 is enough.
- systemd: `Restart=on-failure` (`minibot.service:12`) restarts on any non-zero exit, so 75 is enough. The unit has no `StartLimitBurst` tuning, so systemd's default crash-loop limit applies to a bad boot.
- No supervisor: the process exits 75 and logs `restart required`. The pending set waits for the next manual start. That is a safe failure. In the console, nothing exits; the console reports "restart needed".

Alternative, deferred (section 12): `os.execv` of `sys.orig_argv` after the drain. It keeps the PID, so it works under Docker's PID 1 and in a bare shell with no supervisor. Its downside is that it leaves worker children orphaned unless they are stopped first.

### 5.3 Pending turns and replay: what is true today

- Replay is real. A surviving row is republished at boot (`daemon.py:194-211`). This covers a hard kill or a crash mid-turn.
- Graceful stop mid-turn does not replay. `_handle_message` runs in its own task (`dispatcher.py:459`). Cancellation cascades to that task, and its `finally` clears the row (`dispatcher.py:574`). A standalone asyncio check of the same await-a-task pattern confirmed the `finally` runs on cancel. The turn is dropped silently.
- Replay has no attempt counter. `pending_turns` has no attempts column (`minibot/adapters/memory/pending_turns.py:14-19`). A message that crashes the daemon is replayed on every boot. Evolution's boot counter would roll back the set, but the poison turn would still crash the next boot.
- Queued, not-started messages live only in memory (`dispatcher.py:326`). A restart loses them, so the idle gate must require an empty backlog.
- Task leases outlive the process. A running task stays leased until the lease expires (`minibot/adapters/tasks/sqlite_store.py:154-155`, default 2100 s at `schema.py:1041`), then it is redelivered up to `max_attempts`. A restart during delegated work delays it and may repeat its side effects.

These must be fixed or accepted before PR4 (section 10): an attempt cap on pending rows, a decision on whether graceful stop keeps rows, and the idle predicate above.

## 6. Boot safety

`state.json` (single writer, guarded by a lock file, written through a temp file and `os.replace`):

- `candidate`: module name to approved sha256, for the pending set.
- `promoted`: module name to sha256 of the last set that reached healthy. Rollback source.
- `loaded`: the set the running daemon imported. Workers read this too.
- `boot`: `attempt_id`, `started_at`, `healthy`, `consecutive_failures`.
- `restart_requested`, `notify_chat` (the chat that last requested activation).

Directory layout under `[evolution] directory`:

```text
candidates/   source written by evolution_write_module
promoted/     copies of the set that reached healthy (rollback source)
loaded/       exact files the running daemon imports; on sys.path
state.json, audit.jsonl, DISABLED (kill switch), .git/ (history for the owner), .lock
```

Boot algorithm, daemon entrypoint only (console and worker never write markers):

1. Before `load_extensions` (`minibot/adapters/container/app_container.py:92`): if the previous `boot.healthy` is false, increment `consecutive_failures`. A restart requested by the daemon does not count.
2. If `consecutive_failures >= max_failed_boots`: copy `promoted/` into `loaded/` (rollback). If rollback itself fails, use the empty set (core only). Notify the owner.
3. Otherwise copy `candidates/` for the pending set into `loaded/`. Refuse any file whose sha256 differs from the approved hash.
4. Write a new `boot` attempt with `healthy: false` before any import.
5. Import from `loaded/` only. Put `loaded/` on `sys.path`, appended, so stdlib and site-packages win. The `evo_` prefix and the find-spec check rule out shadowing.
6. Healthy: after `dispatcher.start()` and extension start, the daemon stays up for `healthy_after_seconds` with no unhandled error (a timer task in `daemon.run`, `daemon.py:78-92`). Then copy `loaded/` to `promoted/`, set `healthy: true`, reset the failure count.
7. Replay: boot replay (`daemon.py:194`) uses the attempt cap from section 5.3, so a poison turn is dropped and reported rather than replayed forever.

What counts as healthy is in-process. Do not use `/health`: it is static and off by default. A hung boot (an import that never returns) is not detected in v1; that is a residual risk, and an external watchdog is the answer.

## 7. Safety summary

- Approval binds to bytes. The approved sha256 is written to state and checked again at boot. Changing the file after approval makes boot refuse it.
- Kill switch. Boot-time `enabled = false` removes everything. The runtime file `DISABLED` is checked before every evolved tool call, before activation, and before a restart. Creating the file is always allowed.
- Versioning. `.git/` in the workspace, one commit per activation, for the owner's `git log`. Rollback does not use git: it copies `promoted/` back, so it works without git at boot.
- Ceilings the code enforces:
  - Name pattern `evo_[a-z][a-z0-9_]{2,40}`.
  - Files only under `candidates/`, `loaded/`, `promoted/`. No `..`, no symlinks.
  - Size and count limits.
  - No name that resolves to an existing module (`importlib.util.find_spec`).
  - No tool name that collides with the registry.
  - Evolved contexts get `vault=None` and no agent-management or refresh callbacks. Note: this is cooperative. The code can still import `AppContainer`.
- Ceilings shown but not enforced: an AST scan lists the imports and flags `subprocess`, `socket`, `ctypes`, `importlib`, `multiprocessing`, `shutil`, `pty`, `signal`, and calls to `eval`, `exec`, `__import__`, `compile`. The owner sees the list in the approval. It is a review aid. `getattr` tricks defeat it, so it is not a ceiling and it does not block.
- Audit. `audit.jsonl` is append-only JSON lines: `ts`, `action` (write, validate, approve, deny, activate, promote, rollback, boot_rollback, restart), `module`, `sha256`, `approval_id`, `approved`, `user_id`, `chat_id`, `outcome`, `attempt_id`. The rate limits read this file back as structured records.
- Rate limits: `activations_per_day`, `restarts_per_hour`, `max_modules`, `max_module_bytes`.
- Owner recovery without the model: create `DISABLED`, or restart with `enabled = false`, or (PR5) run an owner CLI command that rolls back.

## 8. Trust model

Consistent with the roadmap's Trust model. MiniBot's responsibility: approvals, safe defaults, rollback, rate limits, no silent config edits. Deployment's responsibility: isolation.

The feature protects against:

- The model activating code through the evolution tools without an owner approval.
- Drift between the approved bytes and the loaded bytes, at activation and at boot.
- Repeated self-breakage: boot counter, rollback, empty-set fallback.
- Churn: rate limits.
- Silent edits to `config.toml` or the agent ceiling. Evolution never writes them.
- Accidental name shadowing or tool-name collisions.

It does not protect against:

1. Host-write tools as the same OS user. `bash` writes anywhere the daemon can. `python_exec` is on by default and runs with no isolation (`schema.py:720`, `:724`). `apply_patch` can edit files under its workspace. Any of them can write `candidates/` or `state.json` directly and skip the gate. This is why `accept_host_write_tools` exists. With it set, the approval is advisory against this model.
2. Approval that does not read the code. The owner can approve code they did not read. The source is sent in full, but reading is the owner's job.
3. Activated code. It runs in-process with the daemon's full privileges. Python has no capability boundary inside a process.
4. Secrets in source. The model writes the source, and the provider sees it. A literal secret in code leaves through the context. Secret-shape detection belongs to Phase 5.
5. Network use by activated code. It can call any host the daemon can reach.
6. Hung boots (section 6).
7. Activated tools reaching generic task workers. Off by default (`worker_access = false`), but if the owner turns it on, they run there too.

The honest sentence for the docs: evolution makes self-change explicit, reviewed, and reversible. It does not make the daemon safe to run unisolated with a host-write tool enabled.

## 9. Layering

Mini hex layout (`AGENTS.md`):

| Piece | Where | Why there |
|---|---|---|
| `EvolutionConfig` | `minibot/config/schema.py` (`Settings` at `:1240`, `ExtensionsConfig` at `:1148`) | Shared config contract. Validator next to `_validate_specialist_activation` (`:1278`). |
| Record types, `EvolutionStateStore` protocol | `minibot/core/evolution.py` (new) | Domain shapes and port. No I/O. |
| Atomic state store, lock, audit writer | `minibot/adapters/evolution/state_store.py`, `audit.py` (new) | Filesystem adapters. Pattern: `managed_store.py:51`. |
| Confined workspace paths | `minibot/adapters/evolution/workspace.py` (new) | Path rules, size and count, `os.replace`. |
| Validator runner and check entry | `minibot/adapters/evolution/validator.py`, `check.py` (new) | Subprocess adapter and the `python -I` entry. |
| Boot decision, promotion, rollback choice | `minibot/app/evolution_boot.py` (new) | Pure logic, testable without a daemon. |
| Policy (limits, kill switch, forced approval) | `minibot/app/evolution_policy.py` (new) | Like `minibot/app/managed_agent_policy.py:32`. |
| Activation, rollback, restart coordinator | `minibot/app/evolution_service.py` (new) | Orchestration and approval calls. |
| Tools | `minibot/extensions/tools/evolution.py` (new) | Bundled `register(mb)`, daemon only, like `agent_management.py:23-24`. Add to the daemon branch of `_bundled_modules` (`minibot/app/extensions.py:56-57`). |
| Extension loader hooks | `minibot/app/extensions.py`: `load_extensions` (`:316`), loop (`:333`), `ExtensionContext` (`:82`, new `evolved` field; `vault` at `:101`, set at `:341-351`), `ExtensionRegistry.evolved_tool_names` (`:221`) | Evolved modules are passed in as extra modules, marked `evolved`, and get no vault. |
| Approval forcing | `minibot/app/tool_factory.py:70-77` | Evolved tool names join the approval patterns. |
| Boot wiring | `minibot/adapters/container/app_container.py:54` (`configure`), `:92` (`load_extensions`) | Decide the set before loading. Daemon entrypoint only. |
| Restart and exit code | `minibot/app/daemon.py:24` (`run`), `:78-92` (timer and wait), `:215` (`_graceful_shutdown`), `:247` (`main`) | `main` exits 75 on a restart request. |
| Idle predicate | `minibot/app/dispatcher.py:114`, `:116`, `:329`, new `is_idle()` near `:255` | Uses existing state. |
| Attempt cap on replay | `minibot/adapters/memory/pending_turns.py:14`, `daemon.py:194` | New column, or a counter table, plus a cap. |
| Worker grant control | `minibot/app/tasks/worker.py:225`, `:234-235`, `:472` | Exclude evolved names unless `worker_access`. |
| Docs | `docs/config.rst`, new `docs/evolution.rst` (add to `docs/index.rst`), `docs/extensions.rst:132-143` (operational rules), `docs/extension-api.rst` (`evolved`), `docs/security.rst` (trust note), `config.example.toml` (commented block) | Per AGENTS.md, every config option is documented. |

## 10. Phased plan

Each PR is small and holds its own contracts. Tests follow the repo's practice: few, high-value, end to end where practical.

- **PR1, config and validation (no behavior).** `EvolutionConfig`, the `Settings` validator, `config.example.toml`, `docs/config.rst`.
  - Tests: two in `tests/test_config_schema_orchestration.py` style. One: off by default, unknown key rejected. One: directory overlap and host-write-without-ack are refused.

- **PR2, restart and boot safety (no tools).** `core/evolution.py`, `state_store.py` with lock, `evolution_boot.py`, `AppContainer.configure` decision before `load_extensions` (`app_container.py:92`), `load_extensions` takes extra modules, daemon healthy timer and exit 75 (`daemon.py:24`, `:247`), `Dispatcher.is_idle()`, the attempt cap (section 5.3, gap 2).
  - Tests: state round trip (save, then load, same shape). One parametrized test of the boot decision table. One daemon test in the style of `tests/test_daemon.py:193` that a restart request exits 75. One test that `is_idle` is false with a backlog.

- **PR3, author and validate (ungated, no effect).** `workspace.py`, `validator.py`, `check.py`, `audit.py`, `evolution.py` tools `status`, `write_module`, `validate`.
  - Tests: one end to end with a real subprocess. A good module passes. A module that reuses a bundled tool name fails. One for traversal and symlink refusal.

- **PR4, activate with approval, restart, kill switch.** `evolution_service.py`, `evolution_activate`, forced approval patterns (`tool_factory.py:70-77`), evolved context with `vault=None` (`extensions.py:341-351`), `worker_access` exclusion (`worker.py:234-235`), `DISABLED` checks, rate limits.
  - Tests: a denied approval leaves state unchanged. Changing the file after validation is refused. A restart waits for an idle daemon. Gap 1 (graceful stop keeps rows or not) must be decided before this PR.

- **PR5, rollback, boot notice, docs.** `evolution_rollback`, the boot-rollback notice as an `OutboundEvent` to `notify_chat` (`minibot/core/events.py:25`), `docs/evolution.rst`, the doc notes, an owner CLI for rollback and status (optional).
  - Tests: one end to end. A candidate that raises in `register` is activated, boots twice, and the daemon comes up on the promoted set with a notice published.

Later (v2, each needs its own design):

- Skills authored by the model, written to `[tools.skills] write_path`. No restart needed (`schema.py:848`).
- Prompt fragments from evolved modules. The hook already exists (`extensions.py:190`).
- `restart = "exec"` (section 5.2).
- Codemode exclusions once Phase 13 lands (section 11).
- An owner-side diff view on a page (`add_page`, `extensions.py:202`).
- A blocking import ceiling, only if the owner asks for one.

Explicitly out of scope, now and later without a new decision: activation without approval, `pip` or any package install, writing `config.toml` or the ceilings, editing MiniBot's source, autonomous spawning of agents or tasks, hot reload in-process.

## 11. Interactions

- Phase 13 codemode (branch `docs/roadmap-codemode`, commit `b92ebd9`, not on HEAD). The planned `run_tools_script` wraps the final tool list after `apply_tool_approval`, so evolved tools called from a script still ask per call. Add `evolution_*` to its exclusion list, so a script cannot loop over the evolution tools. Codemode's Monty sandbox does not constrain activated modules, which are in-process Python. Both edit the same ROADMAP lines, so the merge needs coordination.
- Phase 10 ACP. An external agent can author files inside the workspace only through the bash-like path (section 8, item 1). Out of scope for this design.
- Phase 5 secret-shape detection applies to the source the model writes (section 8, item 4).
- Phase 11 observability can later show evolution events from `audit.jsonl`.

## 12. Open questions, with recommendations

1. Gate authoring too? Recommend no. Nothing runs before activation, and gating it creates approval fatigue.
2. Restart by exit 75 or by `os.execv`? Recommend exit 75 with the documented supervisor contract. Add `exec` later if bare-metal owners ask for it.
3. Accept host-write tools? Recommend refuse by default. Keep `accept_host_write_tools = true` as the explicit acknowledgement, and document that the gate is advisory against this model while it is set.
4. Evolved tools in generic task workers? Recommend off (`worker_access = false`).
5. How does the owner read the code? Recommend `send_file` of the full module before the approval prompt, and the sha256 in the prompt.
6. Which chat is told about a boot rollback? Recommend the chat that last requested activation, stored in `state.json`. No new config option.
7. Gate rollback? Recommend no. It is removal only, and it is rate-limited and audited.
8. Make the import ceiling blocking? Recommend no in v1. Show it as a review aid, as described in section 7.
9. Fix the shutdown gap (section 5.3, gap 1)? Recommend yes, keep pending rows on a shutdown-driven cancel so the turn is replayed. This needs the attempt cap first, or a poison turn can loop. Decide before PR4.
10. Idle timeout and what happens after it? Recommend a 10-minute wait, then keep the restart pending and tell the owner. Do not force-restart a busy daemon.
