# Minibot Extensions

Status: **Phases 1-6 done.** Extensions contribute tools, subscribe to events, and now
carry a channel: Telegram runs as a bundled extension and `daemon.py` no longer knows it
exists. Phase 7 migrates the rest.

Note on §2 below: it is the original analysis, kept as the record of why this was
done. Some of it now describes fixed problems — §2.4's back-pressure blocker and
§2.6's `${ENV_VAR}` example in particular. See the phase notes for what actually
shipped.

---

## 1. Problem

Minibot is 21,296 LOC in one package, and everything is wired in by name at
startup. Adding anything means editing core files:

- **A tool** requires three edits: a `ToolFeature` in `minibot/llm/tools/factory.py:283`,
  a field on `ToolsConfig` in `minibot/adapters/config/schema.py:635`, and a
  `descriptions/*.txt` file.
- **A channel** requires editing `minibot/app/daemon.py:47` (Telegram is
  instantiated by name) and `minibot/adapters/config/schema.py:770`, where
  `Settings.channels` is typed `dict[str, TelegramChannelConfig]` — a second
  channel cannot even be configured.
- **Anything with its own config** is impossible:
  `Settings.model_config = ConfigDict(extra="forbid")`
  (`minibot/adapters/config/schema.py:783`) rejects unknown sections outright.
- **Reacting to what the bot does** is impossible: the event bus carries 5 event
  types (`minibot/core/events.py`), all of them inbound messages or outbound
  responses. Turn lifecycle, tool calls, delegation, compaction and errors are
  computed and then dropped into log lines.

The goal: pull a defensible core out of the rest, and let Python extensions
subscribe to internal events and contribute tools/services — the way a
pi-harness agent emits events subscribers act on.

**Non-goal:** splitting the repo into separate distributions. The boundary moves
in-tree first. What leaves the repo is a later decision, made after real
extensions exist.

---

## 2. Analysis

### 2.1 Size by area

| Area | LOC | Verdict |
|---|---|---|
| `core/` | 484 | **Core.** Pure dataclasses + protocols, no deps. |
| `shared/` | 841 | **Core.** Utils. |
| `app/` (bus, dispatcher, turn pipeline, registries) | 4,992 | **Mostly core**, ~1,000 splittable |
| `adapters/config` | 1,260 | **Core** — but `extra="forbid"` blocks extensions |
| `adapters/container` | 275 | **Core** |
| `adapters/memory` + `sqlalchemy_utils` | 681 | **Core** (history is not optional) |
| `adapters/files` | 365 | **Core-ish** — many tools require it |
| `adapters/messaging/console` | 97 | **Core** (only always-available channel) |
| `adapters/messaging/telegram` | 863 | **Extension** |
| `adapters/messaging/rabbitmq` | 150 | **Extension** (already an extra) |
| `adapters/mcp` | 389 | **Extension** (already an extra) |
| `adapters/qdrant` + `rag/` | 738 | **Extension** (deps not even Poetry-managed) |
| `adapters/scheduler` + `app/scheduler_service.py` | 756 | **Extension** |
| `adapters/tasks` + `app/task_consumer_service.py` | ~1,000 | **Extension** |
| `llm/services/` | 2,057 | **Core.** Provider loop, request building, compaction. |
| `llm/tools/` | 6,514 | **~30% core, ~70% extension** |

`llm/tools/` is the largest single chunk and the most obviously splittable:

- **Core (~1,000):** `base.py`, `factory.py`, `schema_utils.py`, `arg_utils.py`,
  `action_dispatcher.py`, `description_loader.py`, `output_spill.py`,
  `pre_response.py`, `chat_memory.py`, `agent_delegate.py`
- **Extension candidates (~5,500):** `python_exec` (817), `patch_engine` +
  `apply_patch` (670), `file_storage` (518), `scheduler` (423), `http_client` (416),
  `rag_tools` (347), `user_memory` (302), `grep` (258), `bash` (250),
  `mcp_bridge` (212), `calculator` (190), `audio_transcription` (184),
  `skill_loader` (157), `tasks` (142), `code_read`, `time`, `wait`

### 2.2 What is already extension-shaped

Three file-driven mechanisms already exist and are the right model to generalize:

- **Agents** — `agents/*.md`, frontmatter + body → `load_agent_specs`
  (`minibot/app/agent_definitions_loader.py:19`). Drop a file, get a specialist.
- **Skills** — `SkillRegistry` (`minibot/app/skill_registry.py:14`) with discovery
  paths and fingerprint-based staleness refresh. Hot-reloads.
- **MCP servers** — config-declared out-of-process tools, `_build_mcp_feature`
  (`minibot/llm/tools/factory.py:190`).

**MCP is already the extension protocol for anything that can live
out-of-process.** A Python extension system only earns its place for what MCP
cannot do: subscribe to internal events, and run in-process with container access.

### 2.3 Where the tool registration seam is

```
Call stack (tool registration today):
  daemon.py:44             run()
    └─ dispatcher.py:37    Dispatcher.__init__()
        └─ factory.py:79   build_enabled_tools(settings, ...)
            └─ factory.py:104  for feature in _OPTIONAL_FEATURES   ← closed tuple
                └─ feature.builder(context, tools)
```

The `ToolFeature` dataclass (`minibot/llm/tools/factory.py:63`) is already the
right abstraction. It just isn't open.

### 2.4 Event bus back-pressure — blocker

```python
# minibot/app/event_bus.py:37
async def publish(self, event: BaseEvent) -> None:
    await asyncio.gather(*(queue.put(event) for queue in list(self._subscribers)))
```

Bounded queues (`maxsize=128`) + `await queue.put` + `gather` means **one slow
subscriber stalls the entire bus**, including inbound message delivery. With 2-3
trusted subscribers it never bites. The moment third-party code subscribes, it is
a hang waiting to happen.

Minor, same file: `EventSubscription.__aiter__` (`minibot/app/event_bus.py:19`)
only calls `task_done()` after the consumer resumes, so a subscriber that `break`s
leaves the count unbalanced. Harmless today — `join()` is never used.

### 2.5 Proposed core

```
core/                     domain models + protocols
shared/                   utils
app/event_bus.py          + lifecycle event publishing
app/dispatcher.py         event loop
app/handlers/**           turn pipeline
app/agent_registry, skill_registry, agent_runtime
llm/services/**           provider loop, request building, compaction
llm/tools/{base,factory,schema_utils,arg_utils,action_dispatcher,
           description_loader,output_spill,pre_response,chat_memory,
           agent_delegate}.py
adapters/{config,container,logging,memory,files}
adapters/messaging/console
```

≈ 9,500 LOC core, ≈ 11,800 LOC extension-eligible.

Bundled first-party extensions (telegram, scheduler, tasks, rag, the optional
tools) load through the **same** mechanism third parties use. If our own tools
bypass the extension API, it rots.

### 2.6 The mechanism

**Discovery: config-declared modules, not entry points.**

```toml
[extensions]
modules = ["minibot_weather", "myrepo.ext.jira"]

[extensions.config.minibot_weather]
api_key = "${WEATHER_KEY}"
units = "metric"
```

`importlib.import_module` + call `register(mb)`. ~15 lines. Works for
pip-installed packages *and* local modules on `PYTHONPATH`, which matters for a
self-hosted bot where people write one-file extensions.
`importlib.metadata.entry_points()` layers on later for pip-installed ones — same
`register()` function, different discovery.

**Registration object:**

```python
def register(mb: ExtensionContext) -> None:
    mb.on("turn_completed", log_usage)                  # subscribe to events
    mb.add_tool(WeatherTool(mb.config).bindings())      # contribute tools
    mb.add_service(MyChannelService(mb.config, mb.event_bus))  # long-running
```

`ExtensionContext` exposes `config` (this extension's dict), `event_bus`,
`settings`, `logger`, and container getters. Nothing else. Handlers are
`async def handler(event) -> None`.

**Where loading lands:**

```
  daemon.py:26            run()
    └─ AppContainer.configure()                   ← load extension modules HERE
    └─ dispatcher.py:37   Dispatcher.__init__()
        └─ factory.py:79  build_enabled_tools()   ← merge extension tools HERE
    └─ daemon.py:52       services = [...]        ← append extension services HERE
```

Roughly 150-200 LOC of new core code.

### 2.7 Explicitly skipped

- **No plugin sandboxing / permissions.** Self-hosted; you install what you trust.
  Add when there is a marketplace.
- **No API versioning.** Pre-1.0. Add when we break someone.
- **No hot reload for extensions.** Skills hot-reload because they are markdown;
  Python modules do not reload cleanly. Restart the daemon.
- **No extension-owned description files.** Extensions pass description strings
  inline. `load_tool_description` (`minibot/llm/tools/description_loader.py:8`)
  stays hardcoded to `minibot.llm.tools.descriptions`.
- **No repo split.** See non-goal above.

---

## 3. Phases

Phases 1 and 2 are worth doing whether or not extensions ever ship.

### Phase 1 — Typed subscriptions ✅ done

Prerequisite for everything else. Standalone value.

Scope changed during planning: the doc originally called for the lossy drop
policy here. The real blocker for Phase 2 turned out to be that *every*
subscriber received *every* event, so high-volume lifecycle events would flood
128-slot queues belonging to subscribers that don't want them. Filtering at
subscription time fixes that at the source. **The lossy drop policy moved to
Phase 4**, where its first consumer (third-party subscribers) actually lives.

- [x] `minibot/app/event_bus.py` — `subscribe(types=...)`; `None` keeps
      receive-everything, so no call site was forced to change
- [x] `_Subscriber` record pairs each queue with its filter; `publish()` skips
      non-matching queues entirely so the event never occupies a slot
- [x] `stop()` still sends the `None` sentinel to every queue regardless of filter
- [x] `minibot/app/event_bus.py` — `task_done()` now runs before `yield`, so a
      consumer that `break`s does not unbalance the count
- [x] `minibot/app/dispatcher.py:33` — `types=(MessageEvent, OutboundFormatRepairEvent)`
- [x] `minibot/adapters/messaging/telegram/service.py:56` — `types=(OutboundEvent, OutboundFileEvent)`
- [x] `minibot/adapters/messaging/console/service.py:39` — `types=(OutboundEvent,)`
- [x] Test: `tests/test_event_bus.py::test_event_bus_respects_subscription_type_filter`

### Phase 2 — Lifecycle events ✅ done

Useful on its own for observability. No extension machinery needed.

- [x] `minibot/core/events.py` — added `TurnStartedEvent`, `TurnCompletedEvent`,
      `TurnFailedEvent`, and a single `ToolCallEvent` carrying
      `phase: Literal["started", "completed", "failed"]` rather than three classes
- [x] `minibot/app/dispatcher.py` — publish `TurnStartedEvent` in `_handle_message`
- [x] `minibot/app/dispatcher.py` — publish `TurnCompletedEvent` reusing the
      already-computed `token_trace` / `llm_provider` / `llm_model` /
      `compaction_performed`
- [x] `minibot/app/dispatcher.py` — publish `TurnFailedEvent` where the exception
      was previously only logged
- [x] **`ToolCallEvent` via handler wrapper, not the executor.** New
      `minibot/llm/tools/tool_events.py` (~75 LOC) mirrors
      `apply_tool_output_spill`; applied as the outer wrap in
      `build_enabled_tools` (`minibot/llm/tools/factory.py`). The three
      tool-execution call sites (`provider_factory.py:192`,
      `generation_loop.py:182`, `agent_runtime.py:231`) were **not** touched.
      No-ops when `event_bus is None`, so the task worker path is unaffected.
- [x] Payloads carry argument *keys* only — never values or results (size + secrets)
- [x] Every publish in the wrapper is `try`/`except`-guarded: a stopped bus during
      shutdown must never fail a tool in flight
- [x] Turn correlation: `turn_id` added to `ToolContext`
      (`minibot/llm/tools/base.py`), set from `event.event_id` at
      `minibot/app/handlers/services/turn_service.py:95`
- [x] Test: `tests/test_dispatcher.py::test_dispatcher_publishes_turn_lifecycle_events`
      (started ×2, completed, failed)
- [x] Test: `tests/test_tool_factory.py::test_apply_tool_call_events_emits_started_completed_and_failed`
- [ ] Agent delegation start/end (`minibot/llm/tools/agent_delegate.py`) — **deferred.**
      Delegated agents share the same binding list, so their tool calls already emit
      events; only the delegation span itself is missing.

### Phase 3 — Open the config ✅ done

The hard blocker. Nothing below works without it.

- [x] `minibot/adapters/config/schema.py` — `ExtensionsConfig` with
      `modules: list[str]` and `config: dict[str, dict[str, Any]]`
- [x] `extensions: ExtensionsConfig` added to `Settings`; top-level
      `extra="forbid"` kept. Arbitrary keys are allowed only *inside*
      `[extensions.config.<name>]`, because that field's value type is
      `dict[str, Any]` — no normalizer change was needed.
- [x] `config.example.toml` — documented `[extensions]`
- [x] Test: unknown top-level section still raises; arbitrary keys under
      `[extensions.config.foo]` accepted
- [ ] ~~Verify `${ENV_VAR}` substitution reaches `[extensions.config.*]`~~ —
      **dropped, false premise.** No such substitution exists anywhere:
      `_load_file_data` (`schema.py:39`) is a bare `tomllib.load`, and `${` appears
      zero times in `config.toml`, `config.example.toml`, `config.yolo.toml`.
      `CLAUDE.md` claims this feature; correcting that doc is separate work.

### Phase 4 — Extension loading + tools ✅ done

- [x] **Lossy subscriptions** (moved here from Phase 1): `subscribe(types=..., lossy=True)`
      uses `put_nowait` and drops with a warning on `QueueFull`. Core subscribers keep
      blocking semantics; only extensions are lossy.
- [x] **Shutdown deadlock fixed while doing it.** `EventSubscription.close()` and
      `EventBus.stop()` both used a blocking `put` for the stop sentinel, which
      deadlocks whenever the queue is full — exactly the state a stalled subscriber
      leaves it in. Now `_put_sentinel()` evicts to make room; pending events no
      longer matter at shutdown. Caught by `pytest-timeout`, added this phase.
- [x] `minibot/app/extensions.py` (new) — `ExtensionContext`, `ExtensionRegistry`,
      `load_extensions()`
- [x] Loader error handling — **decided: fail loudly.** A missing module, a module
      with no `register`, or a `register()` that raises stops startup with the module
      named. A handler that raises at runtime is logged and its subscription survives.
- [x] `ExtensionContext` surface — **decided: curated, not the raw container.**
      `name`, `config`, `settings`, `event_bus`, `logger`, `on()`, `add_tool()`,
      `add_service()`.
- [x] `app_container.py` — extensions load at the **end** of `configure()`, after
      every backend exists; `get_extensions()` added
- [x] `factory.py` — `build_enabled_tools(extension_tools=...)`, merged before
      `_ensure_unique_tool_names` so collisions with built-ins raise. Extension tools
      inherit `ToolCallEvent` emission and output spill for free.
- [x] `dispatcher.py` — passes `AppContainer.get_extensions().tools`
- [x] **Both** entrypoints start/stop the registry — `daemon.py` and `console.py`
- [x] Tests: `tests/test_extensions.py` (load + event delivery + all three failure
      modes), plus `pytest-timeout` added as a dev dep and applied to the bus/
      dispatcher/extension tests that can hang

### Phase 5 — Prove the API ✅ done

- [x] **Decided: a real example extension, not the `calculator` migration.**
      Moving a bundled tool would have forced a `_BUNDLED_MODULES` auto-load list
      this phase *and* kept `[tools.calculator]` in `ToolsConfig` anyway (or every
      existing `config.toml` breaks on `extra="forbid"`). That decision belongs in
      Phase 7, once, for all bundled tools.
- [x] `examples/minibot_ext_demo.py` — contributes a `demo_greet` tool, subscribes to
      `TurnCompletedEvent`, and reads its own config slice
- [x] `examples/README.md` — how to write one, and the sharp edges
- [x] Proven end-to-end with `console --once`:
      `PYTHONPATH=examples minibot console --once "...greet Ana..."` →
      `extension loaded ... tools=['demo_greet'] subscriptions=1`, assistant replied
      `hola, Ana!` (config slice reached the tool), and the subscriber logged
      `demo extension saw a completed turn`. Failure mode confirmed separately: a
      non-existent module aborts startup naming it.
- [ ] Revise `ExtensionContext` based on what hurt — nothing did; revisit after a
      second real extension exists.

### Phase 6 — Services and channels ✅ done

- [x] `minibot/adapters/config/schema.py` — `ChannelsConfig` replaces
      `dict[str, TelegramChannelConfig]`. `telegram` stays validated; `extra="allow"`
      keeps any other `[channels.<name>]` section as a raw dict, reachable via
      `settings.channels.section(name)`. This also closed a silent trap: `[channels.slack]`
      used to validate *as a Telegram config* and drop every key.
      `settings.channels["telegram"]` → `settings.channels.telegram` (2 prod call sites).
- [x] `minibot/adapters/container/app_container.py` — `get_telegram_config()` deleted;
      `daemon.py` was its only caller.
- [x] `minibot/app/daemon.py` — no longer imports or instantiates `TelegramService`.
      It starts via `extensions.start()`, now *before* `_replay_pending_turns` — harmless,
      the outbound subscription is created in `__init__` during `configure()` either way.
- [x] **Bundled extensions**, the question deferred from Phase 5:
      `_BUNDLED_MODULES = ("minibot.extensions.telegram",)` in `minibot/app/extensions.py`,
      loaded ahead of user modules. They cannot go in `[extensions] modules` — every
      existing `config.toml` would silently lose Telegram.
- [x] `minibot/extensions/` (new) — thin `register(mb)` entry points only. The 863 LOC of
      `adapters/messaging/telegram/` did **not** move; "bundled extension" means loaded
      through the extension API, not relocated. This is where Phase 7's modules go.
- [x] `mb.add_service()` contract — **already shipped in Phase 4.** `ExtensionRegistry`
      (`minibot/app/extensions.py`) drives `start()`/`stop()` and the registry is already in
      the daemon's `_graceful_shutdown` service list. Nothing to do; now covered by
      `tests/test_extensions.py::test_registry_starts_and_stops_contributed_services`.
- [x] Console channel stays core.
- [x] **`ExtensionContext.entrypoint`** (`"daemon"` | `"console"`) — not in the original
      plan, and the phase does not work without it. `console.py` calls `extensions.start()`,
      so a bundled Telegram would have booted a poller under `minibot console --once`.
      Not fixable at `start()` time: `TelegramService.__init__` takes a *blocking*
      subscription and only drains it in `start()`, so a built-but-unstarted service fills
      its 128-slot queue and stalls every `publish`. The service must not be *constructed*
      ⇒ the check belongs in `register()`. Threaded through
      `AppContainer.configure(..., entrypoint=...)`.
- [x] Verified: `minibot console --once` logs the extension with `services=0` and never
      starts polling; the daemon logs `services=1`. (The daemon's own polling line needs
      network; the start path itself is the unchanged Phase 4 registry code.)

### Phase 6b — Extension tools inside task workers

Found in review of Phase 6. An extension's tools reach the main agent and delegated agents
but **not** a task spawned with `spawn_task`, so the agent can call `demo_greet` in chat and
then watch a worker report it does not exist.

Not a deliberate exclusion. `minibot/adapters/tasks/worker.py` is a forked `Process`
(`adapters/tasks/manager.py:87`) that calls `load_settings()` itself
(`worker.py:97`) and never calls `AppContainer.configure()` — the only place
`load_extensions()` runs. It then assembles tools with `_build_worker_tools`
(`worker.py:161`), a hand-maintained duplicate of `build_enabled_tools`. Extensions were
added to the `build_enabled_tools` path only, and the worker's copy never saw them.

Documented as a known limit in `examples/README.md` for now.

- [ ] `entrypoint="worker"` — the flag from Phase 6 already generalizes. The worker loads
      extensions, takes `registry.tools`, and ignores subscriptions and services: it has no
      bus to drive them and no lifecycle to hang them on
- [ ] `ExtensionContext.event_bus` is typed `EventBus` and the worker has none
      (`worker.py:184` already passes `event_bus=None` to `FileStorageTool`). Either hand it
      a throwaway `EventBus()` — safe, `publish` with no subscribers is a no-op — or widen the
      field to `EventBus | None`. Decide once; the field is public API
- [ ] Document that an observe-only extension contributes nothing inside a worker, since
      `registry.start()` is never called there
- [ ] **Import cost:** loading extensions in the worker imports `_BUNDLED_MODULES`, which
      imports aiogram into every forked worker even though Telegram's `register()` returns
      early for a non-daemon entrypoint. Either make `_BUNDLED_MODULES` entrypoint-aware or
      measure and accept it
- [ ] Test: a tool contributed by an extension is callable from inside a spawned task
- [ ] **Root cause, optional:** `_build_worker_tools` duplicating `build_enabled_tools` is
      why this drifted. Collapsing them is a bigger change than this phase and wants its own
      decision — the worker deliberately builds a narrower set (no delegation, no chat memory)

### Phase 7 — Migrate remaining bundled extensions

Ordered by isolation, easiest first. Each is independent — stop whenever the
return stops justifying the churn.

- [ ] `rag` + `qdrant` (738 LOC) — deps are not even Poetry-managed, cleanest cut
- [ ] `mcp` (389 LOC) — already an extra
- [ ] `rabbitmq` (150 LOC) — already an extra
- [ ] `scheduler` (756 LOC) — `adapters/scheduler` + `app/scheduler_service.py`
- [ ] `tasks` (~1,000 LOC) — `adapters/tasks` + `app/task_consumer_service.py`
- [ ] Remaining optional tools: `python_exec`, `bash`, `apply_patch`,
      `file_storage`, `http_client`, `grep`, `user_memory`, `audio_transcription`,
      `code_read`, `time`, `wait`
- [ ] `ARCHITECTURE.md` — update the layout map and document the core/extension line
- [ ] Docs: writing an extension (discovery, `register()`, event catalog)

---

## 4. Open questions

- [x] Extension load failure — **fail startup loudly**, with the module named. A
      silently absent tool leaves the agent quietly unable to do something, which is
      much harder to diagnose than a boot crash. A handler that raises at *runtime*
      is logged and its subscription survives.
- [x] `ExtensionContext` surface — **curated**, not the raw `AppContainer`: `name`,
      `config`, `settings`, `event_bus`, `logger`, `on()`, `add_tool()`,
      `add_service()`. The container is a class-level singleton; handing it over
      would make every field public API.
- [ ] Do extension event handlers get to *modify* anything, or observe only?
      (still observe-only, as shipped — mutation means ordering and conflict rules)
- [x] Do bundled extensions ship in the `minibot` package or as `minibot_contrib.*`? —
      **`minibot/extensions/`**, in-package, answered in Phase 6. Thin `register(mb)`
      modules over adapter code that stays where it is. A separate distribution is still
      the non-goal from §1.
