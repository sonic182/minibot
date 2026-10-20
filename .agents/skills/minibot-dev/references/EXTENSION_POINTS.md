# Extension Points

Where each kind of change lands, with the wiring chain that explains *why* it lands there.
References name a file and a symbol, never a line number: find the definition with `find_symbol`
(gmem) or `rg`.

---

## 1. A new LLM tool

File templates and the step-by-step wizard are owned by the `minibot-create-tool` skill. This
section is the wiring behind them.

### Extension path (default)

```
daemon.run()                                   app/daemon.py
  └─ AppContainer.configure()                  adapters/container/app_container.py
      └─ load_extensions(...)                  app/extensions.py
          └─ importlib.import_module(name)     ← _bundled_modules(...) then [extensions].modules
              └─ your_module.register(mb)      ← YOUR CODE
                  └─ mb.tool / mb.add_tool
  └─ Dispatcher._build_tools                   app/dispatcher.py
      └─ build_enabled_tools(extension_tools=...)   app/tool_factory.py
          ├─ tools.extend(extension_tools)
          └─ _ensure_unique_tool_names(tools)
  └─ Dispatcher._main_agent_view
      └─ main_agent_tool_view(...)             app/tool_capabilities.py
```

A tool that ships with MiniBot takes the same path: a bundled module under
`minibot/extensions/tools/` listed in `_bundled_modules`, reading its `[tools.<key>]` config
from `mb.settings`. See `minibot/extensions/tools/utility.py`.

`ExtensionContext` (`app/extensions.py`) is deliberately narrow — this is the whole surface:

| Member | Notes |
|---|---|
| `mb.name` / `mb.config` / `mb.settings` | `config` is the `[extensions.config.<module>]` slice; `settings` is full validated `Settings` |
| `mb.event_bus` / `mb.logger` / `mb.entrypoint` | entrypoint is `"daemon" \| "console" \| "worker"` |
| `mb.vault` | unlocked vault or `None`; never expose a secret to the LLM. Always `None` in workers |
| `mb.agent_registry` / `mb.agent_management` / `mb.refresh_tools` | registry is updated in place after registration; read specs when needed |
| `mb.on(EventType[, handler])` | decorator or direct call; extension subscriptions are **lossy** |
| `mb.tool(func)` | name = `__name__`, description = docstring, schema = first arg's pydantic model |
| `mb.add_tool(binding)` | escape hatch for a custom name, hand-written schema, or file-loaded description |
| `mb.add_tool_deriver(deriver)` | tools built per agent from its final tool list (e.g. codemode); must be pure |
| `mb.add_service(service)` | anything with async `start()`/`stop()` |
| `mb.add_route` / `mb.add_prompt_fragment` / `mb.add_page` | HTTP routes, system-prompt fragments (optionally tied to tool names), web UI pages |

There is no `mb.container` and no DI accessors. Construct anything else inside `register()`
from `mb.settings`. `load_extensions` treats an import or `register()` failure as fatal.

### Core path (only what an extension cannot build)

Lean core (SKILL.md, `docs/architecture.md`): a tool goes here only when it needs state
`ExtensionContext` does not expose. If the gap is generic, propose widening `ExtensionContext`
instead. Today's core set, in `build_enabled_tools` (`app/tool_factory.py`):

```
ChatMemoryTool       always                                   needs: conversation memory backend
SettingsInfoTool     always                                   needs: config path, agent + skill names
SkillLoaderTool      if [tools.skills].enabled                needs: skill registry, managed storage
                     (via build_skill_loader_bindings, app/tool_constructors.py)
SkillInstallerTool   if [tools.skills].install                needs: skill registry
AgentInfoTool        if [orchestration.specialists].enabled   needs: agent registry, LLM client factory
                     and the registry is non-empty
```

Create: `minibot/llm/tools/<module>.py` exposing `.bindings() -> list[ToolBinding]`, and
`minibot/llm/tools/<tool_name>.txt` for each description.
Edit: a config model + a field on `ToolsConfig` (`config/schema.py`), a branch in
`build_enabled_tools`, `config.example.toml`, and the docs (`docs/tools.rst`).

**Worker visibility is separate.** See §6 — a tool wired only into `tool_factory.py` does not exist
for task workers.

---

## 2. A new messaging channel

There is **no channel registry**. A channel is a service class plus an extension that builds it.

```
AppContainer.configure(entrypoint="daemon")    adapters/container/app_container.py
  └─ load_extensions(...)                      app/extensions.py
      └─ extensions/channels/telegram.py::register(mb)
          ├─ guard: if mb.entrypoint != "daemon": return    ← mandatory
          ├─ guard: config.enabled and credentials present
          └─ mb.add_service(TelegramService(...))
daemon.run() → extensions.start() → service.start()
```

The service contract, by example (`adapters/messaging/telegram/service.py`,
`adapters/messaging/console/service.py`):

- `__init__` subscribes to the outbound events it renders, e.g.
  `event_bus.subscribe(types=(OutboundEvent, OutboundFileEvent, …))`. A subscription made in
  `__init__` is blocking: an unstarted service fills its queue and stalls every publish, which
  is why the entrypoint guard must return before construction.
- `async start()` — spawn the inbound poll task and the outbound drain task
- `async stop()` — close the subscription, cancel the tasks
- inbound: build `ChannelMessage(channel="<name>", ...)` (`core/channels.py`) and
  `await event_bus.publish(MessageEvent(message=...))`
- outbound: consume `OutboundEvent.response: ChannelResponse` and render `RenderableResponse`
- optional: on a markup parse failure, publish `OutboundFormatRepairEvent` so the Dispatcher
  asks the model to repair the formatting (see `adapters/messaging/telegram/outbound_sender.py`)

Channel behaviour flags (tool approval, reply targets, format-repair retry, file delivery) are a
`ChannelCapabilities` (`core/channels.py`) passed to the `Dispatcher` as the
`channel_capabilities` mapping built in `app/daemon.py` (and `app/console.py`). A channel absent
from that mapping gets the all-false defaults.

Create: `minibot/adapters/messaging/<name>/service.py`, `minibot/extensions/channels/<name>.py`.
Edit: the `daemon` branch of `_bundled_modules` (`app/extensions.py`) if bundling it;
otherwise just list the module under `[extensions].modules`.

Config: either a typed model plus a field on `ChannelsConfig` (`config/schema.py`), **or** the
escape hatch — `ChannelsConfig` is `extra="allow"` and `ChannelsConfig.section("<name>")`
returns the raw `[channels.<name>]` dict. The escape hatch is the intended
path for out-of-tree channels.

Optional prompt fragment: `prompts/channels/<name>.md` — see PROMPT_COMPOSITION.md.

Two channels are *not* extensions: `app/console.py` constructs `ConsoleService` directly, and
`app/daemon.py` constructs `WebChannelService` (`adapters/messaging/web/`) when `[http] enabled`.

---

## 3. A new specialist agent

Pure file drop — **no Python edit**. Create `agents/<name>.md` (the `[orchestration].directory`;
Docker mounts `./local_agents` there).

```
AppContainer.configure()
  └─ load_active_agent_specs(settings, reader=LocalAgentDefinitionReader())   app/agent_definitions_loader.py
      ├─ returns [] unless [orchestration.specialists].enabled
      ├─ load_agent_specs([orchestration].directory)          owner-authored
      │   └─ reader.read → sorted *.md                        adapters/agents/definition_reader.py
      │   └─ parse_agent_definition
      │       ├─ split_frontmatter / parse_frontmatter        shared/frontmatter.py
      │       ├─ AgentDefinitionConfig.model_validate         config/schema.py
      │       └─ → AgentSpec                                  core/agents.py
      └─ load_agent_specs([orchestration.agent_management].directory, strict_name=True)
          └─ ManagedAgentPolicy.authorize(spec)               model-authored only
  └─ AgentRegistry(agent_specs)                               app/agent_registry.py
Dispatcher → build_enabled_tools(agent_registry=...) → AgentInfoTool → fetch_agent_info
Delegation is delegate / spawn_task, from the tasks extension (delegate: result returns to the model;
spawn_task: answer goes to the user; [tasks].delegation_mode = "delegate" registers only delegate):
  extensions/services/tasks.py → TaskTools → delegate | spawn_task → TaskManager → worker subprocess
PromptService._specialist_roster_fragment                     app/handlers/services/prompt_service.py
```

Frontmatter is a hand-rolled YAML subset (scalars, 2-space lists, 2-space dicts) — not real
YAML. Fields are validated by `AgentDefinitionConfig`; `tools_allow` and `tools_deny` are
mutually exclusive. The Markdown body becomes the specialist system prompt and **must not be
empty** (that is fatal); a malformed name only warns for owner files, and is an error for
managed ones. Two files with the same agent name are an error.

The `[orchestration.agent_management]` ceiling (`tools_allow`, `mcp_servers`, `providers`)
bounds only model-authored agents; owner-authored files are not checked against it.

Read the tool-scoping semantics in CONVENTIONS.md before setting `tools_allow` / `tools_deny` —
they do not mean what they appear to mean for MCP tools.

The worker (`app/tasks/worker.py`) and the tasks extension (`extensions/services/_task_wiring.py`)
load agent specs themselves, so an agent is available to `spawn_task` and `delegate` without
extra wiring.

---

## 4. A new config section

```
AppContainer.configure()
  └─ load_settings(path)                    adapters/config/loader.py
      ├─ resolve_config_path   (arg → $MINIBOT_CONFIG → ./config.toml)   config/paths.py
      └─ Settings.from_file → from_dict     config/schema.py
          ├─ expand_environment(...)        config/environment.py
          ├─ _expand_secret_references(...) (resolves only when secrets are passed)
          ├─ _normalize_for_annotation(...)
          └─ Settings.model_validate        (extra="forbid")
  └─ second load_settings(path, vault secrets) when ${secret:} syntax is present
```

Create: nothing. Edit: a `BaseModel` in `config/schema.py` (keep the docstring
convention — `"""… TOML section: ``[x]``"""`, with each field listed; it is rendered into
`docs/config.rst`), a field on `Settings` or on the relevant parent (`ToolsConfig`,
`ChannelsConfig`), then `config.example.toml` and `docs/config.rst`. Add to
`adapters/config/configurator.py` only if it belongs in `minibot configure`.

Two things to know:

- **`Settings` is `extra="forbid"`** — an unknown top-level section is a hard boot failure.
  The two deliberate escape hatches that let you add config *without* touching the schema are
  `ChannelsConfig.section(...)` and `[extensions.config.<module>]`.
- **Interpolation runs on every string value**, extension config included — see
  CONVENTIONS.md (*Config environment and secret interpolation*).

Byte-size fields use `ByteSizeValue` (`config/schema.py`), which accepts strings like `"10MB"`.

---

## 5. A new event handler or dispatcher hook

`EventBus` (`app/event_bus.py`) is a bounded (`maxsize=128`) in-process async pub/sub. Core
subscribers are blocking and apply back-pressure; **extension subscribers are lossy and drop
under pressure**.

Event catalog — `core/events.py`, every class subclassing `BaseEvent`: messages
(`MessageEvent`, `OutboundEvent`, `OutboundFileEvent`, `OutboundFormatRepairEvent`,
`SystemEvent`), turn lifecycle (`TurnStartedEvent`, `TurnInputPreparedEvent`,
`TurnCompletedEvent`, `TurnFailedEvent`, `TurnStopRequestedEvent`, `ReasoningEvent`) and tools
(`ToolCallEvent`, `ToolApprovalRequestedEvent`, `ToolApprovalResolvedEvent`).

**(a) Observe an existing event — no core edit.**

```
register(mb) → @mb.on(TurnCompletedEvent)      app/extensions.py (ExtensionContext.on)
  └─ ExtensionRegistry.start()
      └─ event_bus.subscribe(types=(EventType,), lossy=True)
      └─ ExtensionRegistry._drive   ← handler exceptions are logged and swallowed
```

Example: `examples/minibot_ext_demo.py`.

**(b) Emit a new event type.** Add the model to `core/events.py` (subclass `BaseEvent`, set
`event_type`), publish via `mb.event_bus.publish(...)`. Tool lifecycle events are emitted
automatically by the `apply_tool_call_events` wrapper (`llm/tools/tool_events.py`).

**(c) Handle a new event type in the Dispatcher** — `app/dispatcher.py`:

```
Dispatcher.__init__   widen event_bus.subscribe(types=(MessageEvent, OutboundFormatRepairEvent, TurnStopRequestedEvent))
Dispatcher._route     add an isinstance branch (handle inline, or append to the backlog)
Dispatcher._work      drains the backlog one event at a time; add the branch for a queued type
Dispatcher._handle_message / Dispatcher._handle_format_repair — add a sibling handler
```

**(d) A new turn-stage collaborator, not an event.** Add it to `app/handlers/services/`,
export it from that package's `__init__.py`, construct it in `build_llm_turn_service`
(`handlers/services/turn_service.py`), and pass it to `LLMTurnService`. The main path is
`Dispatcher._work → _run_turns → _handle_message → LLMMessageHandler.handle → LLMTurnService.handle`.

---

## 6. Task workers

Workers are **forked subprocesses** with a deliberately narrower assembly than the daemon.

```
delegate / spawn_task (LLM tools)
  └─ producer.enqueue(TaskRequest)         core/tasks.py  (TaskProducer protocol)
      ├─ SQLiteTaskProducer                adapters/tasks/sqlite_store.py
      └─ RabbitMQTaskProducer              adapters/messaging/rabbitmq/producer.py
  └─ consumer leases the row
  └─ TaskManager  app/tasks/manager.py  → forks worker_entry over a pipe
      └─ worker_entry(pipe)                adapters/tasks/worker_process.py  (builds WorkerBackends)
          └─ worker_entry(pipe, backends)  app/tasks/worker.py
              └─ run_agent_loop(...)       app/tasks/worker.py
                  ├─ backends.load_settings(secrets)
                  ├─ load_extensions(..., entrypoint="worker")   ← scheduler + utility + worker_extensions + user modules
                  ├─ _resolve_task_spec(...)
                  ├─ _build_worker_tools(...)
                  ├─ _with_derived_tools(...)
                  └─ AgentRuntime(...)
```

Two traps:

- **Workers load only the scheduler and utility bundles** (`_bundled_modules`,
  `app/extensions.py`). Besides them, only user-configured `[extensions].modules` load there,
  and bundled tool modules self-narrow when `mb.entrypoint == "worker"` (e.g. `read_docs`).
  To give workers a bundled extension tool, add its module to the worker branch. A bundle with a
  service (kv_memory, rag) or an opt-in feature (codemode) is opt-in instead: it is listed in
  `WORKER_OPT_IN_MODULES` and the `[tasks] worker_extensions` literal, and a service-backed one
  wraps its tools with `start_on_first_call` (`extensions/_lazy_start.py`), because worker
  registries never start services. Opt-in tools are left out of the generic worker's allowlist
  (`ExtensionRegistry.tools_outside` in `run_agent_loop`): only a specialist that claims them
  gets them.
- **Workers build their core tools independently of `tool_factory.py`.** To make a core tool
  worker-visible, add it to `_build_worker_tools` **and** `_WORKER_TOOL_ALLOWLIST`
  (both in `app/tasks/worker.py`). Extension tools are auto-appended and auto-allowlisted for
  the generic worker; a named specialist gets them filtered by its own `tools_allow`/`tools_deny`.

The default worker spec (`_build_worker_spec`) sets `max_tool_iterations=None`; steps and tool calls
are bounded by `[tasks].worker_max_steps` / `worker_max_tool_calls` instead.

For a new queue backend: implement `TaskProducer` (`core/tasks.py`) plus a consumer service,
add a backend literal to `TasksConfig` (`config/schema.py`), and add its bundled module to
`_bundled_modules`, mirroring `extensions/services/tasks.py` and
`extensions/integrations/rabbitmq.py`.

---

## 7. A skill for MiniBot's own runtime

MiniBot implements the agentskills.io spec itself, so a skill in this repo is both a
development skill *and* something the bot can `activate_skill` at runtime.

```
AppContainer.configure()
  └─ SkillRegistry.from_config([tools.skills])        app/skill_registry.py
      ├─ resolve_skill_discovery_paths     app/skill_definitions_loader.py
      ├─ fingerprint_skill_paths           (path, mtime_ns, size)
      └─ load_skill_specs → parse_skill_file → SkillSpec   core/skills.py
Dispatcher → build_enabled_tools(skill_registry=...)
  └─ if [tools.skills].enabled → build_skill_loader_bindings → SkillLoaderTool
PromptService._skill_catalog_fragment       app/handlers/services/prompt_service.py
runtime: SkillRegistry.refresh_if_stale()   reloads on a changed fingerprint
```

Create: `<discovery-path>/<skill-name>/SKILL.md`. **No code change** — the registry hot-reloads
on an mtime/size fingerprint, unlike prompt files which are cached for the process lifetime.

Discovery order (`resolve_skill_discovery_paths`): `[tools.skills].write_path` (default
`~/.minibot/skills`), then either `[tools.skills].paths` or the defaults from
`_default_discovery_paths` (`./.minibot/skills`, `./.agents/skills`, `~/.minibot/skills`,
`~/.agents/skills`), then the native skills in `minibot/skills/` when `[tools.skills].native`.
Setting `paths` replaces only the defaults. In this repo `.claude/skills` is a symlink to
`.agents/skills`, so write new skills to `.agents/skills/`.

Frontmatter constraints are in CONVENTIONS.md — MiniBot's parser differs from the spec.
