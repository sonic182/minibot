# Minibot Extensions

Status: **planning** — nothing implemented yet.

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

### Phase 1 — Fix event bus back-pressure

Prerequisite for everything else. ~10 lines. Standalone value.

- [ ] `minibot/app/event_bus.py:31` — allow a subscription to be marked
      non-blocking (`subscribe(lossy=True)` or equivalent)
- [ ] `minibot/app/event_bus.py:37` — `publish()` uses `put_nowait` for lossy
      subscribers; on `QueueFull`, drop and log a warning with the event type
- [ ] Core subscribers (dispatcher, telegram, console) keep blocking semantics
- [ ] `minibot/app/event_bus.py:19` — call `task_done()` before `yield` so a
      consumer that `break`s does not unbalance the count
- [ ] Test: a full lossy queue does not block `publish()` to other subscribers

### Phase 2 — Lifecycle events

Useful on its own for observability. No extension machinery needed.

- [ ] `minibot/core/events.py` — add `TurnStartedEvent`, `TurnCompletedEvent`,
      `TurnFailedEvent`, `ToolCallEvent`
- [ ] `minibot/app/dispatcher.py:150` — publish `TurnStartedEvent` in
      `_handle_message`
- [ ] `minibot/app/dispatcher.py:169` — publish `TurnCompletedEvent` carrying the
      already-computed `token_trace`, `llm_provider`, `llm_model`,
      `compaction_performed`
- [ ] `minibot/app/dispatcher.py:230` — publish `TurnFailedEvent` instead of only
      logging the swallowed exception
- [ ] `minibot/llm/services/tool_executor.py` — publish `ToolCallEvent` pre/post
      (needs an optional `event_bus` reference threaded in; keep it optional so
      the executor stays usable without a bus)
- [ ] Decide whether agent delegation start/end
      (`minibot/llm/tools/agent_delegate.py`) is in scope here or deferred
- [ ] Test: one turn end-to-end emits started → tool calls → completed

### Phase 3 — Open the config

The hard blocker. Nothing below works without it.

- [ ] `minibot/adapters/config/schema.py` — add `ExtensionsConfig` with
      `modules: list[str]` and `config: dict[str, dict[str, Any]]`
- [ ] `minibot/adapters/config/schema.py:768` — add `extensions: ExtensionsConfig`
      to `Settings`; keep top-level `extra="forbid"`
- [ ] Verify `${ENV_VAR}` placeholder substitution reaches inside
      `[extensions.config.*]` (`minibot/adapters/config/loader.py`)
- [ ] `config.example.toml` — document an `[extensions]` section
- [ ] Test: an unknown top-level section still errors; an unknown key under
      `[extensions.config.foo]` is accepted

### Phase 4 — Extension loading + tools

Smallest useful extension surface: events + tools.

- [ ] `minibot/app/extensions.py` (new, ~50 LOC) — `ExtensionContext`,
      `load_extensions(settings, event_bus, ...)`: import each module, call
      `register(ctx)`, collect tools / subscribers / services
- [ ] Loader error handling — a broken extension logs and is skipped, or fails
      startup loudly. **Decide which** (recommend: fail loudly at startup, since a
      silently-missing tool is worse than a crash on boot)
- [ ] `minibot/adapters/container/app_container.py:52` — load extensions after
      settings and event bus exist; add `get_extensions()`
- [ ] `minibot/llm/tools/factory.py:79` — accept extension bindings; extend
      `tools` before `_ensure_unique_tool_names` (`factory.py:107`) so name
      collisions raise, which is already the right behavior
- [ ] `minibot/app/dispatcher.py:37` — pass extension bindings into
      `build_enabled_tools`
- [ ] `minibot/app/daemon.py:52` — start extension event subscribers as tasks;
      stop them in `_graceful_shutdown` (`daemon.py:122`)
- [ ] Test: a fixture extension contributing one tool and one event handler is
      loaded, its tool reaches the LLM, its handler receives a `TurnCompletedEvent`

### Phase 5 — Prove the API

If this is awkward, the API is wrong. Do this before moving anything else.

- [ ] Move `calculator` (190 LOC, zero deps) to load through the extension path
- [ ] Keep it enabled by default — bundled extensions must not require config
      changes from existing users
- [ ] Confirm no regression in `tests/test_tool_factory.py`
- [ ] Revise `ExtensionContext` based on what hurt

### Phase 6 — Services and channels

Largest change; leave for last.

- [ ] `minibot/adapters/config/schema.py:770` — `Settings.channels` typed
      `dict[str, TelegramChannelConfig]`; needs a base channel config + per-channel
      typing so a second channel can be configured
- [ ] `minibot/app/daemon.py:47` — stop instantiating `TelegramService` by name;
      resolve channels through the extension/service registry
- [ ] `mb.add_service()` contract: `start()` / `stop()`, wired into
      `_graceful_shutdown` (`minibot/app/daemon.py:122`)
- [ ] Move Telegram (863 LOC) to a bundled extension
- [ ] Console channel stays core (only always-available channel)

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

- [ ] Extension load failure: fail startup vs. skip and log? (leaning: fail loudly)
- [ ] Do extension event handlers get to *modify* anything, or observe only?
      (leaning: observe only for v1 — mutation means ordering and conflict rules)
- [ ] Should `ExtensionContext` expose `AppContainer` directly, or only a curated
      set of getters? (leaning: curated — the container is a class-level singleton
      and handing it over makes every field public API)
- [ ] Do bundled extensions ship in the `minibot` package or as
      `minibot_contrib.*`? (defer until Phase 7 actually starts)
