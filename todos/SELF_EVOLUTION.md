# Self-evolution behind an opt-in (`[evolution]`)

Status: design only, no code. Phase 14 (priority 7) in `todos/ROADMAP.md`.
Line numbers were checked at commit `8aa85bf`; no file under `minibot/` changed after it.

Findings that change the brief:

- There is no `entrypoint.sh` and no active Docker `HEALTHCHECK`. The only healthcheck in `docker-compose.yml` is a commented example for an optional RabbitMQ service (`docker-compose.yml:7-12`). The image runs `CMD ["minibot"]` (`Dockerfile:75`) with `restart: unless-stopped` (`docker-compose.yml:24`).
- `/health` exists only when `[http] enabled` is true (default false, `minibot/config/schema.py:1205`). It returns a static `{"status": "ok"}` (`minibot/adapters/http/server.py:49-50`), so it is a liveness check, not a readiness check.
- The codemode code is not on main. Its plan, Phase 13 in `todos/ROADMAP.md`, is on main (#128). Section 11 covers the interaction.
- Pending-turn replay does not cover every restart. Section 5 lists three pre-existing gaps that must be fixed before a restart can be trusted.

## 1. Problem, goals, non-goals

Problem. The owner wants MiniBot to add its own tools and services. Today that means someone edits `[extensions] modules`, writes Python by hand, and restarts the daemon.

Goals for v1, all behind `[evolution] enabled = true`:

- The model can author a standalone extension in a dedicated workspace.
- The model can check it statically before asking for activation. Only static checks run before the owner's approval.
- The model can request activation. Activation needs the approval of the configured owner (`[evolution] owner_user_id`) for the exact bytes approved. The first import of the module runs after that approval, in a throwaway subprocess.
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

- Extensions load once, in `AppContainer.configure` (`minibot/adapters/container/app_container.py:92`). The loader is `load_extensions` (`minibot/app/extensions.py:316`). It imports bundled modules, then `[extensions] modules` (`extensions.py:333`). Any import or `register` failure is fatal (`extensions.py:336-356`). Every extension receives the unlocked vault (`extensions.py:101`, set at `:341-351`), and the resolved `Settings` (`extensions.py:97`, passed at `:344`).
- The worker calls `load_extensions` again for every task, after reloading settings (`minibot/app/tasks/worker.py:225`). So a new module reaches workers on the next task, while the daemon still runs the old set. Workers import evolved modules too (section 6, step 5). `docs/extensions.rst:132-143` says worker registries start no services or subscriptions and contribute tools only. That stays true for evolved modules: a worker imports and registers them, starts none of their services or subscriptions, and `worker_access` only controls whether their tools are granted.
- The registry is started once (`minibot/app/daemon.py:81-83`). Services, subscriptions and HTTP routes are built at boot (`daemon.py:95-191`). `Dispatcher.refresh_tools` only rebuilds tools from the same extension list (`minibot/app/dispatcher.py:255`). Hot reload of a module would need all of that rebuilt.
- Tool approval wraps tools whose names match `[tools.approval] require_approval` (`minibot/app/tool_factory.py:70-77`, `minibot/app/tool_approval.py:33-50`). The question goes to the chat that started the call (`tool_approval.py:83-94`). A channel without approval support denies (`tool_approval.py:96`). The console does not support approval (`minibot/core/channels.py:9`). A `detail` string is capped at 3000 characters (`tool_approval.py:22`, `:178`).
- Agent management is the model-authored precedent. Writes go through `AgentManagementService._persist` and `_authorize` (`minibot/app/agent_management.py:110`, `:151`). The owner ceiling is `ManagedAgentPolicy.authorize` (`minibot/app/managed_agent_policy.py:60`). The store writes through a temp file and `os.replace` (`minibot/adapters/agents/managed_store.py:51`), with a 64 KB cap (`:10`).
- Skills already have a runtime write path, `[tools.skills] write_path`. A skill written there is found without a restart (`minibot/config/schema.py:848` docstring).
- Replay exists: `PendingTurnStore` marks a turn before it starts (`minibot/app/dispatcher.py:462`) and `_replay_pending_turns` republishes surviving rows at boot (`minibot/app/daemon.py:194-211`, called at `:90`). The row is cleared in a `finally` (`dispatcher.py:573-574`).
- Task workers are `multiprocessing.Process` children (`minibot/app/tasks/manager.py:328`). SQLite task leases last 2100 s by default (`minibot/config/schema.py:1041`).
- `bash` has no filesystem jail (`docs/security.rst`, trust section). `python_exec` is enabled by default with `sandbox_mode = "basic"`, which means same process user and no isolation (`minibot/config/schema.py:720`, `:724`; `minibot/llm/tools/python_exec.py:41`).
- `send_file` resolves paths under managed storage only (`minibot/llm/tools/file_storage.py:293-306`). Its outbound event is `OutboundFileEvent` (`minibot/core/events.py:30`).
- Docker mounts `./config.toml` read-only (`docker-compose.yml:34`), `./data` read-write (`:37`), and the source `./minibot` read-write (`:38`). The image installs `git` (`Dockerfile:22`).
- MCP tools are named `<name_prefix>_<server>__<tool>` (`minibot/llm/tools/mcp_bridge.py:136`, lazy catalog names at `:326`). The default prefix is `mcp` (`minibot/config/schema.py:808`).

## 3. Configuration: `[evolution]`

New `EvolutionConfig` in `minibot/config/schema.py`, added to `Settings` next to `extensions` (`schema.py:1256`). It uses `extra="forbid"` like the other gated sections.

| Option | Default | Why |
|---|---|---|
| `enabled` | `false` | Master switch. Off means: no evolution tools, no workspace read, no `sys.path` change, no boot markers. |
| `owner_user_id` | none, required when enabled | Telegram user id of the one owner. Only this user can approve activation, and the prompt goes to this id's private chat. It must be in `[channels.telegram] allowed_user_ids`. It is not `[runtime].owner_id` (`minibot/config/schema.py:179`), which names the memory owner and is not a Telegram id. |
| `directory` | `"./data/evolution"` | Workspace root. Under `./data` so it persists in Docker (`docker-compose.yml:37`), and not under the source mount (`:38`). |
| `max_modules` | `8` | Bounds how much model-authored code is loaded at once. |
| `max_module_bytes` | `64000` | Same ceiling as managed agent definitions (`managed_store.py:10`). |
| `activations_per_day` | `5` | Limits approval prompts and churn. Counted from `audit.jsonl`. |
| `restarts_per_hour` | `3` | Caps restart storms even when each activation was approved. |
| `max_failed_boots` | `2` | Unhealthy boots of one set before falling back to the promoted set. Another `max_failed_boots` unhealthy boots of the promoted set fall back to core only. |
| `healthy_after_seconds` | `60` | How long the daemon must stay up after boot before a set is promoted. |
| `worker_access` | `false` | Whether evolved tools are granted to generic task workers. Today every user extension tool is granted (`worker.py:234-235`, `:472`). Evolved code should not inherit that by default. |
| `accept_host_write_tools` | `false` | Explicit acknowledgement that `bash`, `python_exec` (not `jail`) or `apply_patch` can bypass the gate (section 8). |

Validation rules, in the `Settings` model validator next to `_validate_specialist_activation` (`schema.py:1278`). When `enabled` is false, only the types are checked.

- Approval needs Telegram and the owner. Require `[channels.telegram] enabled`, and `owner_user_id` in `[channels.telegram] allowed_user_ids`, which must be non-empty. If `[channels.telegram] allowed_chat_ids` is non-empty, it must contain `owner_user_id`, since a private chat id equals the user id. Telegram is the only channel with both approval and file delivery (`minibot/adapters/messaging/telegram/capabilities.py:4-7`). The web channel approves but does not deliver files (`minibot/adapters/messaging/web/capabilities.py:3`). A daemon without a reachable owner chat would deny or fail every activation, so refuse to start in that state.
- No root the model's tools can write to may contain the workspace or be contained by it (equality counts). The roots are: the installed `minibot` package directory, `[orchestration].directory`, `[orchestration.agent_management].directory`, `[tools.file_storage].root_dir` (when enabled), `[tools.apply_patch].workspace_root` (when enabled), and `[tools.skills].write_path` (when enabled, expanded as `minibot/app/skill_definitions_loader.py:63` does). A new writable root joins this list. The model's own file tools must not reach the workspace.
- `accept_host_write_tools = false` refuses to start while `[tools.bash] enabled`, `[tools.apply_patch] enabled`, or `[tools.python_exec] enabled` with a `sandbox_mode` other than `jail` is true.
- `worker_access = true` requires `[tasks] enabled`.
- `[extensions] modules` may not name a module that starts with `evo_` (the evolution prefix). This is checked at load.
- `[tools.mcp] name_prefix` must not put `evo_` at the start of an MCP tool name. Refuse when `f"{name_prefix}_"` starts with `evo_` (for example the prefix `evo`). Checked when `[evolution] enabled` is true.

## 4. Capabilities in v1

Registered by a bundled extension, `minibot/extensions/tools/evolution.py`. It is added to the daemon branch of `_bundled_modules` (`minibot/app/extensions.py:56-57`) only when `[evolution] enabled` is true. The `agent_management.py:24` early return skips only the worker. Evolution also returns for the console, which cannot approve (`minibot/core/channels.py:9`). So the console and the worker get no evolution tools.

Tool names. Every tool this feature registers, control or evolved, starts with `evo_` and matches `evo_[a-z][a-z0-9_]{2,58}` (at most 64 characters). Evolved contexts enforce this in `ExtensionContext.add_tool` (`minibot/app/extensions.py:171`). Evolved names must also not collide with the names already registered (section 6, step 5).

| Tool | Gate | Effect |
|---|---|---|
| `evo_status` | none, read-only | Enabled, kill-switch state, candidate / promoted / loaded module names and hashes, failure count, restarts and activations used. |
| `evo_write_module` | none | Writes `candidates/evo_<name>.py` through a temp file and `os.replace`. Confined to the workspace, size and count capped. Refused for a name with a pending activation. No runtime effect. |
| `evo_validate` | none | Static checks on one candidate (below). Records the sha256 it checked. Runs no candidate code. |
| `evo_activate` | owner approval (`owner_user_id` only), always, then the import check | Snapshots the source, sends it to the owner, asks the owner for approval, runs the import check on the snapshot, then sets the pending set to `promoted + {name: sha256}` (or removes `name`). Requests a restart after idle. |
| `evo_rollback` | none, rate-limited | Sets the pending set to `promoted`, requests a restart. Removal only. |

Why authoring has no gate: the static checks execute no candidate code, and the write only touches the workspace, so gating them creates approval fatigue. The first execution of model code is the import check, and it is gated. Why rollback has no gate: it only removes code, and it is still bounded by `restarts_per_hour` and audited.

Static validation (`evo_validate`). Runs in the daemon without importing the candidate: name pattern, size, UTF-8, `ast.parse`, the AST import list (section 7), and `ruff check` if `ruff` is importable (a dev dependency only, `pyproject.toml:101`). Static checks cannot prove that the module imports or what its tools are called. The import check does that.

Redacted settings. Evolved contexts and the import check get a redacted copy of `Settings`, not the live object. It is `settings.model_dump(mode="json")` with (a) the `[vault]` table replaced by its defaults, and (b) each secret-bearing field replaced by a placeholder: a non-empty string becomes `REDACTED`, a dict becomes `{}`, and an empty or `None` value stays as it is. The secret-bearing fields are `bot_token`, `api_key`, `auth_token`, `auth_secret`, `basic_auth_user`, `basic_auth_password`, `headers`, `extra_headers` and `password_file`. The validators read these fields only for presence (`minibot/config/schema.py:600-606`, `:1217-1225`), so the copy validates whenever the live config did. Field defaults would not: `[decision] enabled` with an empty `api_key` is refused, and so is a non-loopback `[http]` host with no auth. A placeholder authenticates nothing, so code that uses it fails with an auth error. A test walks the schema and fails on any other field whose name matches `key|token|secret|password|header|auth|credential`, unless it is on a reviewed allowlist (for example `max_new_tokens`). This is best-effort: a secret in a field outside that rule stays visible (section 8).

Import check (inside `evo_activate`, after approval). A subprocess runs `python -I -m minibot.adapters.evolution.check <name> <result path>` with `cwd` set to the workspace, a cleared environment (allowlist `PATH`, `LANG`), and a 20 s timeout. It is not a sandbox: the code runs as the daemon user with its filesystem and network access. That is why it runs only after approval. The check does the following:

1. Reads the redacted settings as JSON from stdin and builds `Settings` from them. It does not reload `config.toml`: an unset `${VAR}` raises (`minibot/config/environment.py:18`), and it does not inherit daemon environment secrets.
2. Imports `<name>` from `approved/<sha256>/` (section 6), with that directory as the only workspace path on `sys.path`.
3. Builds an evolved `ExtensionContext`: `entrypoint="daemon"`, `evolved=True`, `vault=None`, no agent registry, no agent-management or refresh callbacks.
4. Calls `register(mb)`.
5. Writes one JSON object to the result path, a temp file the parent created: `import_ok`, `register_ok`, tool names, subscription, service and route counts, errors. The parent reads that file, not stdout, because module code can print. stdout and stderr go to the owner's log and are never parsed. A missing or non-JSON result refuses.

The parent then compares the reported tool names, by canonical name (`minibot/llm/services/tool_executor.py:50`), with the live tool list, and refuses on a collision.

MCP limit. MCP tool names are known only after the MCP extension connects, so the import check cannot list them, and the live list can miss tools of a server that is down. The prefix rule in section 3 removes the need to know them: every MCP name starts with `<prefix>_`, and that never starts with `evo_`, so no MCP tool can equal an evolved name. Bundled and `[extensions] modules` names are checked by equality against the live list at activation and again at boot.

Activation flow (`evo_activate`):

1. Check `enabled`, `DISABLED`, `activations_per_day` (counted from `audit.jsonl`), and `max_modules`. Refuse a candidate whose hash is in `rejected`. Any channel may request it, because the prompt always goes to the owner's Telegram chat (step 5).
2. Require a passing `evo_validate` record for the current candidate hash.
3. Read the candidate once, hash those bytes H, and write them to `approved/<H>/<name>.py` (write-once: created with `O_EXCL`, then read-only). The snapshot is taken before the owner sees anything, so the bytes sent, approved and checked are one immutable copy. Mark the name as pending, so `evo_write_module` refuses it until the activation ends.
4. Send the full source, from the snapshot, to the owner's private chat as an `OutboundFileEvent` (`minibot/core/events.py:30`). That is the event `send_file` publishes (`minibot/llm/tools/file_storage.py:306`). `send_file` itself cannot carry it, because it resolves only paths under managed storage and the workspace must stay outside that root (section 3). The Telegram sender accepts any file path (`minibot/adapters/messaging/telegram/outbound_sender.py:83`).
5. Call `request_tool_approval` (`minibot/app/tool_approval.py:83`) with `channel="telegram"`, `chat_id=owner_user_id` and `requester_user_id=owner_user_id`. The Telegram callback then admits only the owner (`minibot/adapters/messaging/telegram/service.py:277-284`). The owner must have opened a private chat with the bot; if the prompt cannot be sent, the request is denied (`service.py:246-253`). The `detail` holds the name, H, size, the static report, and the import list and flags. Tool names come from the import check, which runs after approval, and the prompt says so. The detail is capped at 3000 characters (`tool_approval.py:22`). The tool calls this itself, so the gate does not depend on `[tools.approval]`.
6. On approval, check that the approver's `user_id` is `owner_user_id`. `request_tool_approval` returns only a bool today (`tool_approval.py:83-129`), so PR4 returns the approver's id from `ToolApprovalResolvedEvent` (`minibot/core/events.py:166-170`). Then re-hash the snapshot and refuse if the hash is not H.
7. Run the import check on the snapshot. Refuse on any failure, on a name outside the `evo_` pattern, or on a collision. A refusal changes no state and is audited.
8. Write the pending set, with H, to `state.json`. Append to `audit.jsonl`, clear the pending mark, and request a restart (section 5).

Approval is also required for every later call to an evolved tool: `build_enabled_tools` adds evolved tool names to the approval patterns (`tool_factory.py:70-77`). The console denies (`tool_approval.py:96`).

## 5. Restart design

### 5.1 In-process reload: rejected

Reloading a module in the running process would mean rebuilding the registry, the started services, subscriptions, HTTP routes, prompt fragments and the dispatcher tool list (`daemon.py:81-191`, `extensions.py:278-300`, `dispatcher.py:255`), plus `sys.modules` cleanup. That is a larger change than the feature, and the failure modes are worse than a clean process exit.

### 5.2 Process restart: chosen

The daemon restarts itself by exiting with code 75 (`EX_TEMPFAIL`) after a graceful stop. The steps:

1. `evo_activate`, `evo_rollback` or a quarantine (section 6) writes `restart_requested` to `state.json`.
2. A coordinator waits until the daemon is idle. Idle means: no active turn (`dispatcher.py:116`), an empty backlog (`dispatcher.py:114`, in memory only), no task rows in `leased` or `running` state, and no `OutboundEvent` queued in a subscriber queue. The last condition is not observable today. A finished turn publishes its replies to the bus (`dispatcher.py:524`), and channel services deliver them from their own subscriber queues, but `EventBus` has no queue depth. PR2 adds a count of queued events of one type to `EventBus` (subscriber queues at `minibot/app/event_bus.py:33`), and `Dispatcher.is_idle()` (near `dispatcher.py:255`) uses it. Without it, a reply can be lost at exit. The coordinator polls every second and gives up after about 10 minutes. If it gives up, the restart stays pending and the owner is told.
3. The coordinator runs `_graceful_shutdown` (`minibot/app/daemon.py:215-233`). Then `daemon.main` (`daemon.py:247-271`) exits with 75.

Supervisor contract:

- Docker: `restart: unless-stopped` (`docker-compose.yml:24`) restarts the container on any exit code. Exit 75 is enough.
- systemd: `Restart=on-failure` (`minibot.service:12`) restarts on any non-zero exit, so 75 is enough. The unit has no `StartLimitBurst` tuning, so systemd's default crash-loop limit applies to a bad boot.
- No supervisor: the process exits 75 and logs `restart required`. The pending set waits for the next manual start. That is a safe failure. In the console, nothing exits; the console reports "restart needed".

Alternative, deferred (section 12): `os.execv` of `sys.orig_argv` after the drain. It keeps the PID, so it works under Docker's PID 1 and in a bare shell with no supervisor. Its downside is that it leaves worker children orphaned unless they are stopped first.

### 5.3 Pending turns and replay: what is true today

- Replay is real. A surviving row is republished at boot (`daemon.py:194-211`). This covers a hard kill or a crash mid-turn.
- Graceful stop mid-turn does not replay. `_handle_message` (`dispatcher.py:459`) runs in its own task, created at `dispatcher.py:352`. Cancellation cascades to that task, and its `finally` clears the row (`dispatcher.py:573-574`). A standalone asyncio check of the same await-a-task pattern confirmed the `finally` runs on cancel. The turn is dropped silently.
- Replay has no attempt counter. `pending_turns` has no attempts column (`minibot/adapters/memory/pending_turns.py:14-19`). A message that crashes the daemon is replayed on every boot. Evolution's boot counter would roll back the set, but the poison turn would still crash the next boot.
- Queued, not-started messages live only in memory (`dispatcher.py:326`). A restart loses them, so the idle gate must require an empty backlog.
- Task leases outlive the process. A running task stays leased until the lease expires (`minibot/adapters/tasks/sqlite_store.py:154-155`, default 2100 s at `schema.py:1041`), then it is redelivered up to `max_attempts`. A restart during delegated work delays it and may repeat its side effects.

These must be fixed or accepted before PR4 (section 10): an attempt cap on pending rows, a decision on whether graceful stop keeps rows, and the idle predicate above.

## 6. Boot safety

`state.json` (single writer, guarded by a lock file, written through a temp file and `os.replace`):

- `candidate`: module name to approved sha256, for the pending set. Its files live in `approved/`.
- `promoted`: module name to sha256 of the last set that reached healthy. Rollback source.
- `loaded`: module name to sha256 of the files the running daemon imported. Workers read this too.
- `rejected`: sha256 values that boot and `evo_activate` refuse (quarantine, below).
- `boot`: `attempt_id`, `started_at`, `healthy`, `consecutive_failures`.
- `restart_requested`. Notices go to the owner's private chat, so no chat id is stored.

Directory layout under `[evolution] directory`:

```text
candidates/   source written by evo_write_module
approved/     <sha256>/<name>.py, written once at activation; boot and the import check read only these
promoted/     copies of the set that reached healthy (rollback source)
loaded/       exact files the running daemon imports; on sys.path
state.json, audit.jsonl, DISABLED (kill switch), .lock
```

Boot algorithm. The daemon writes markers. Workers read `state.json` and `loaded/` only. The console loads no evolved module. Quarantine of hashes H means: add H to `rejected`, drop H from `promoted`, set `candidate` to `promoted`, all in one `state.json` write before any import.

1. Kill switch first, daemon and workers. If `enabled` is false or a `DISABLED` file exists, load no evolved module: no extra modules reach `load_extensions`, and no marker is written. The daemon logs it. Otherwise, daemon only: if the previous `boot.healthy` is false, increment `consecutive_failures`. A restart the daemon requested does not count.
2. Daemon only. If `consecutive_failures >= max_failed_boots`, fall back: quarantine the candidate hashes that are not in `promoted`, then copy `promoted/` into `loaded/` and check each file's sha256 against `state.json` `promoted`. On any mismatch or copy error, use the empty set (core only). If `consecutive_failures >= 2 * max_failed_boots`, the promoted set failed too: quarantine it and use the empty set. Notify the owner in both cases.
3. Daemon only. Otherwise copy each file of the pending `candidate` set from `approved/<sha256>/<name>.py` into `loaded/`. Refuse any file whose sha256 differs from the approved hash, or whose hash is in `rejected`. A refusal forces the fallback of step 2 and counts as a failed boot, so `consecutive_failures` goes up by one.
4. Daemon only. One `state.json` write, before any import, holds the result of steps 2 and 3: the quarantine, the `loaded` hashes, a new `boot` attempt with `healthy: false`, and `restart_requested` cleared. Clearing it here, after step 1 has read it, keeps a requested restart from counting as a failure.
5. Daemon and workers. Check each file in `loaded/` against `state.json` `loaded` before importing it, and refuse a mismatch. A partial copy from a daemon boot fails this check and is refused, which is fail-closed. In the daemon, a refused file fails the boot, so the attempt stays unhealthy and the next boot counts it. Put `loaded/` on `sys.path`, appended, so stdlib and site-packages win. The `evo_` prefix and the find-spec check rule out shadowing. A tool name that collides with an earlier registration in the same load (bundled, `[extensions] modules`, MCP, or an earlier evolved module) fails the load. Workers only read: they never copy, roll back or write markers.
5a. Workers only. Today a failing `register` fails the whole task (`minibot/app/extensions.py:353-356`, `minibot/app/tasks/worker.py:354-362`), and the daemon never learns of it. For evolved modules, a worker that refuses a file, or whose import, `register` or name check fails, leaves that module out of its registry, keeps the other tools, and appends one `worker_load_failed` record (module, expected sha256, error) to `audit.jsonl` with one `O_APPEND` write. The task still runs without that module's tools. Skipping is fail-safe: no unvetted code runs, and the owner sees the record.
6. Healthy (daemon only): after `dispatcher.start()` and extension start, the daemon stays up for `healthy_after_seconds` with no unhandled error (a timer task in `daemon.run`, `daemon.py:78-92`). Then copy `loaded/` to `promoted/`, record the hashes as `promoted` (skipping any hash in `rejected`), set `healthy: true`, reset the failure count.
6a. Worker reports (daemon only): a periodic task in `daemon.run`, running for the life of the daemon, reads the `worker_load_failed` records appended since its last read, once a minute. For each record whose sha256 is in `loaded`, quarantine that hash and request a restart. The restart does not count as a failed boot. Quarantine also drops the pending candidates not yet loaded, so the owner approves them again.
7. Replay: boot replay (`daemon.py:194`) uses the attempt cap from section 5.3, so a poison turn is dropped and reported rather than replayed forever.

What counts as healthy is in-process. Do not use `/health`: it is static and off by default. A hung boot (an import that never returns) is not detected in v1; that is a residual risk, and an external watchdog is the answer.

## 7. Safety summary

- Approval binds to bytes and to the owner. The sha256 shown in the approval is the one stored in `candidate`, and boot checks the same hash again. The approved bytes are the write-once snapshot in `approved/`, so editing `candidates/` after approval changes nothing. A tampered snapshot is refused at boot and by the import check. Only `owner_user_id` can approve (section 4, step 6).
- Kill switch. `enabled = false` at boot, or a `DISABLED` file found at boot (section 6, step 1), loads no evolved module in the daemon or in workers. A `DISABLED` file created at runtime blocks evolved tool calls, activation and restarts at once. It does not unload subscriptions, services, routes or prompt fragments that are already running. Those stop at the next start, which loads nothing while the file exists. Creating the file is always allowed.
- Ceilings the code enforces:
  - Module name pattern `evo_[a-z][a-z0-9_]{2,40}`. Tool name pattern `evo_[a-z][a-z0-9_]{2,58}` (section 4).
  - Files only under `candidates/`, `approved/`, `loaded/`, `promoted/`. No `..`, no symlinks.
  - Size and count limits.
  - No module name that resolves to an existing module in the stdlib or site-packages: `importlib.machinery.PathFinder.find_spec` over the `sysconfig` stdlib, purelib and platlib paths only. `loaded/` is not searched, since it holds the evolved modules themselves.
  - Tool names start with `evo_` and do not collide with the names already registered, in the import check at activation and in `add_tool` at boot. MCP names cannot collide, by the prefix rule (section 3).
  - Evolved contexts get `vault=None`, no agent registry, no agent-management or refresh callbacks, and the redacted settings from section 4. This is cooperative, not a boundary. The code can still import `AppContainer`, read `os.environ`, and read `config.toml` and the vault file, which the daemon user can read.
- Ceilings shown but not enforced: an AST scan lists the imports and flags `subprocess`, `socket`, `ctypes`, `importlib`, `multiprocessing`, `shutil`, `pty`, `signal`, and calls to `eval`, `exec`, `__import__`, `compile`. The owner sees the list in the approval. It is a review aid. `getattr` tricks defeat it, so it is not a ceiling and it does not block.
- Audit. `audit.jsonl` is append-only JSON lines, one `O_APPEND` write per record: `ts`, `action` (write, validate, approve, deny, import_check, activate, promote, rollback, boot_rollback, boot_disabled, restart, quarantine, worker_load_failed), `module`, `sha256`, `approval_id`, `approved`, `user_id` (the approver, which must be `owner_user_id`), `requester_user_id`, `chat_id`, `outcome`, `attempt_id`. The rate limits read this file back as structured records.
- Rate limits: `activations_per_day`, `restarts_per_hour`, `max_modules`, `max_module_bytes`.
- Owner recovery without the model: create `DISABLED`, or restart with `enabled = false`.

## 8. Trust model

Consistent with the roadmap's Trust model. MiniBot's responsibility: approvals, safe defaults, rollback, rate limits, no silent config edits. Deployment's responsibility: isolation.

The feature protects against:

- The model activating code through the evolution tools without an owner approval.
- Anyone other than `owner_user_id` approving an activation, including an allowed user who asked for it.
- Model code running before the owner approves it. Only static checks run earlier.
- Drift between the approved bytes and the loaded bytes, at activation and at boot.
- Repeated self-breakage: boot counter, rollback, empty-set fallback.
- Churn: rate limits.
- Silent edits to `config.toml` or the agent ceiling. Evolution never writes them.
- Accidental name shadowing or tool-name collisions.

It does not protect against:

1. Host-write tools as the same OS user. `bash` writes anywhere the daemon can. `python_exec` is on by default and runs with no isolation (`schema.py:720`, `:724`). `apply_patch` can edit files under its workspace. Any of them can write `candidates/`, `approved/` or `state.json` directly and skip the gate. This is why `accept_host_write_tools` exists. With it set, the approval is advisory against this model.
2. Approval that does not read the code. The owner can approve code they did not read. The source is sent in full, but reading is the owner's job.
3. Activated code. It runs in-process with the daemon's full privileges, and Python has no capability boundary inside a process. Its first import runs once, after approval, in the import-check subprocess, which has the same user's filesystem and network access.
4. Secrets in source and in settings. The model writes the source, and the provider sees it. A literal secret in code leaves through the context. Secret-shape detection belongs to Phase 5. Evolved code gets redacted settings (section 4), which is best-effort, and in-process code can still read `os.environ`, `config.toml` and the vault file.
5. Network use by activated code. It can call any host the daemon can reach.
6. Hung boots (section 6).
7. Activated tools reaching generic task workers. Off by default (`worker_access = false`), but if the owner turns it on, they run there too. Import-time code runs in every worker whatever `worker_access` says.

The honest sentence for the docs: evolution makes self-change explicit, reviewed, and reversible. It does not make the daemon safe to run unisolated with a host-write tool enabled.

## 9. Layering

Mini hex layout (`AGENTS.md`):

| Piece | Where | Why there |
|---|---|---|
| `EvolutionConfig` | `minibot/config/schema.py` (`Settings` at `:1240`, `ExtensionsConfig` at `:1148`) | Shared config contract. Validator next to `_validate_specialist_activation` (`:1278`). |
| Record types, `EvolutionStateStore` protocol | `minibot/core/evolution.py` (new) | Domain shapes and port. No I/O. |
| Atomic state store, lock, audit writer | `minibot/adapters/evolution/state_store.py`, `audit.py` (new) | Filesystem adapters. Pattern: `managed_store.py:51`. |
| Confined workspace paths | `minibot/adapters/evolution/workspace.py` (new) | Path rules, size and count, `os.replace`. |
| Import-check runner and check entry (PR4) | `minibot/adapters/evolution/validator.py`, `check.py` (new) | Subprocess adapter and the `python -I` entry. |
| Boot decision, promotion, rollback choice | `minibot/app/evolution_boot.py` (new) | Pure logic, testable without a daemon. |
| Policy (limits, kill switch, forced approval, owner-only approval, settings redaction) | `minibot/app/evolution_policy.py` (new) | Like `minibot/app/managed_agent_policy.py:32`. |
| Activation, rollback, restart coordinator | `minibot/app/evolution_service.py` (new) | Orchestration and approval calls. |
| Tools | `minibot/extensions/tools/evolution.py` (new) | Bundled `register(mb)`, daemon only, like `agent_management.py:23-24`. Add to the daemon branch of `_bundled_modules` (`minibot/app/extensions.py:56-57`) when enabled. |
| Extension loader hooks | `minibot/app/extensions.py`: `load_extensions` (`:316`), loop (`:333`), `ExtensionContext` (`:82`, new `evolved` field; `settings` at `:97`, `vault` at `:101`, set at `:341-351`), `add_tool` (`:171`, prefix and reserved-name check for evolved contexts), `ExtensionRegistry.evolved_tool_names` (`:221`) | Evolved modules are passed in as extra modules, marked `evolved`, get no vault, a redacted settings copy, and no agent registry. In workers, a failing evolved module is skipped and reported (section 6, step 5a). |
| Approval forcing | `minibot/app/tool_factory.py:70-77` | Evolved tool names join the approval patterns. |
| Boot wiring | `minibot/adapters/container/app_container.py:54` (`configure`), `:92` (`load_extensions`) | Decide the set before loading. Daemon and workers only. |
| Restart and exit code | `minibot/app/daemon.py:24` (`run`), `:78-92` (timer and wait), `:215` (`_graceful_shutdown`), `:247` (`main`) | `main` exits 75 on a restart request. |
| Idle predicate | `minibot/app/dispatcher.py:114`, `:116`, `:329`, new `is_idle()` near `:255`; `minibot/app/event_bus.py:33` (subscriber queues), new queued-count method on `EventBus` (PR2) | Uses existing state, plus one count for queued outbound events. |
| Attempt cap on replay | `minibot/adapters/memory/pending_turns.py:14`, `daemon.py:194` | New column, or a counter table, plus a cap. |
| Worker grant control | `minibot/app/tasks/worker.py:225`, `:234-235`, `:472` | Exclude evolved names unless `worker_access`. Import still happens there. |
| Docs | `docs/config.rst`, new `docs/evolution.rst` (add to `docs/index.rst`), `docs/extensions.rst:132-143` (operational rules, add the worker import note), `docs/extension-api.rst` (`evolved`), `docs/security.rst` (trust note), `config.example.toml` (commented block) | Per AGENTS.md, every config option is documented. |

## 10. Phased plan

Each PR is small and holds its own contracts. Tests follow the repo's practice: few, high-value, end to end where practical.

- **PR1, config and validation (no behavior).** `EvolutionConfig`, the `Settings` validator (including the owner, Telegram-only, writable-root and MCP prefix rules), `config.example.toml`, `docs/config.rst`.
  - Tests: two in `tests/test_config_schema_orchestration.py` style. One: off by default, unknown key rejected. One: overlap with any writable root (including `[tools.skills] write_path`), host-write-without-ack, an MCP prefix that yields `evo_`, and an `owner_user_id` outside `allowed_user_ids` are refused.

- **PR2, restart and boot safety (no tools).** `core/evolution.py`, `state_store.py` with lock, `audit.py` (moved from PR3, because workers write to it), `evolution_boot.py`, `AppContainer.configure` decision before `load_extensions` (`app_container.py:92`), `load_extensions` takes extra modules, daemon healthy timer and exit 75 (`daemon.py:24`, `:247`), `Dispatcher.is_idle()` with the queued-outbound count on `EventBus`, the attempt cap (section 5.3, gap 2), and the quarantine and worker handling (section 6, steps 2, 5a and 6a).
  - Tests: state round trip (save, then load, same shape). One parametrized test of the boot decision table, covering `enabled` false, a present `DISABLED`, a promoted sha mismatch, the failure count, and quarantine (after two failed boots the promoted set loads, and the candidate hash is refused at the next boot). One daemon test in the style of `tests/test_daemon.py:193` that a restart request exits 75. One test that `is_idle` is false with a backlog and with a queued `OutboundEvent`. One worker test: a module that fails in a worker is skipped there, one `worker_load_failed` record is appended, and the daemon quarantines the hash and restarts.

- **PR3, author and check statically (no code runs).** `workspace.py`, `evo_status`, `evo_write_module`, `evo_validate` (static checks only).
  - Tests: a good module passes the static checks. One for traversal and symlink refusal. One for an oversize module.

- **PR4, approve, import check, restart, kill switch.** `validator.py`, `check.py`, `evolution_service.py`, `evo_activate`, `evo_rollback`, the `approved/` snapshot, the owner check and the approver id from `request_tool_approval`, the redacted settings, forced approval patterns (`tool_factory.py:70-77`), evolved context with `vault=None`, no agent registry and the `add_tool` checks (`extensions.py:171`), `worker_access` exclusion (`worker.py:234-235`), `DISABLED` checks, rate limits.
  - Tests: a denied approval leaves state unchanged. An approval from anyone but `owner_user_id`, including the requester, is refused. Editing `candidates/` after approval changes nothing, because the snapshot is what gets loaded. One end to end with a real import-check subprocess: a good module passes, and a module that reuses a bundled tool name or lacks the `evo_` prefix fails. The redaction walk test, and a round trip: `Settings.model_validate` on the redacted copy must not raise, for a config with `[decision] enabled` and a non-loopback `[http]` host with auth. A restart waits for an idle daemon. Gap 1 (graceful stop keeps rows or not) must be decided before this PR.

- **PR5, rollback, boot notice, docs.** `evo_rollback`, the boot-rollback notice as an `OutboundEvent` to the owner's private chat (`minibot/core/events.py:25`), `docs/evolution.rst`, the doc notes.
  - Tests: one end to end. A candidate passes the import check, then raises in `register` once the test creates a marker file after activation, which the check cannot see. It boots twice, the daemon comes up on the promoted set, the hash is quarantined, and a notice is published.

Later (v2, each needs its own design):

- Skills authored by the model, written to `[tools.skills] write_path`. No restart needed (`schema.py:848`).
- Prompt fragments from evolved modules. The hook already exists (`extensions.py:190`).
- `restart = "exec"` (section 5.2).
- Codemode exclusions once Phase 13 lands (section 11).
- An owner-side diff view on a page (`add_page`, `extensions.py:202`).
- A blocking import ceiling, only if the owner asks for one.
- An owner CLI for rollback and status (cut from PR5: `evo_rollback` and `DISABLED` cover recovery).
- Git history for the workspace (`.git/`, one commit per activation), for the owner's `git log`. Rollback never needs it.

Explicitly out of scope, now and later without a new decision: activation without approval, `pip` or any package install, writing `config.toml` or the ceilings, editing MiniBot's source, autonomous spawning of agents or tasks, hot reload in-process.

## 11. Interactions

- Phase 13 codemode (plan in `todos/ROADMAP.md` on main, from #128; the code is not on main). The planned `run_tools_script` wraps the final tool list after `apply_tool_approval`, so evolved tools called from a script still ask per call. Add `evo_*` to its exclusion list, so a script cannot loop over the evolution tools. Codemode's Monty sandbox does not constrain activated modules, which are in-process Python.
- Phase 10 ACP. An external agent can author files inside the workspace only through the bash-like path (section 8, item 1). Out of scope for this design.
- Phase 5 secret-shape detection applies to the source the model writes (section 8, item 4).
- Phase 11 observability can later show evolution events from `audit.jsonl`.

## 12. Open questions, with recommendations

1. Gate authoring too? Recommend no. Only static checks run before activation, and gating them creates approval fatigue. The first execution of model code is the approved import check.
2. Restart by exit 75 or by `os.execv`? Recommend exit 75 with the documented supervisor contract. Add `exec` later if bare-metal owners ask for it.
3. Accept host-write tools? Recommend refuse by default. Keep `accept_host_write_tools = true` as the explicit acknowledgement, and document that the gate is advisory against this model while it is set.
4. Evolved tools in generic task workers? Recommend off (`worker_access = false`). Import still runs in workers, so the boot checks and `DISABLED` apply there too.
5. How does the owner read the code? Recommend an `OutboundFileEvent` of the full module before the approval prompt, and the sha256 in the prompt.
6. Which chat is told about a boot rollback? Recommend the owner's private chat (`owner_user_id`). No new config option.
7. Gate rollback? Recommend no. It is removal only, and it is rate-limited and audited.
8. Make the import ceiling blocking? Recommend no in v1. Show it as a review aid, as described in section 7.
9. Fix the shutdown gap (section 5.3, gap 1)? Recommend yes, keep pending rows on a shutdown-driven cancel so the turn is replayed. This needs the attempt cap first, or a poison turn can loop. Decide before PR4.
10. Idle timeout and what happens after it? Recommend a 10-minute wait, then keep the restart pending and tell the owner. Do not force-restart a busy daemon.
