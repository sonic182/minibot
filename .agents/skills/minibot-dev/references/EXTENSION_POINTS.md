# Extension Points

Where each kind of change lands, with the wiring chain that explains *why* it lands there.
References name a file and a symbol, never a line number: find the definition with `find_symbol`
(gmem) or `rg`.

---

## 1. A new LLM tool

### Extension path (default)

```
daemon.run()                                   app/daemon.py
  └─ AppContainer.configure()                  adapters/container/app_container.py
      └─ load_extensions(...)                  app/extensions.py
          └─ importlib.import_module(name)     ← bundled list in _bundled_modules (app/extensions.py)
              └─ your_module.register(mb)      ← YOUR CODE
                  └─ mb.tool / mb.add_tool
  └─ Dispatcher._build_tools                   app/dispatcher.py
      └─ build_enabled_tools(extension_tools=...)   app/tool_factory.py
          ├─ tools.extend(extension_tools)
          └─ _ensure_unique_tool_names(tools)  app/tool_factory.py
      └─ main_agent_tool_view(...)             app/tool_capabilities.py
```

Create: one module with `def register(mb: ExtensionContext) -> None`.
Edit: `[extensions].modules` in config, plus `[extensions.config.<module>]` for its settings.
Do **not** touch `schema.py` or `tool_factory.py` on this path.

A tool that ships with MiniBot takes the same path: a bundled module under
`minibot/extensions/tools/` added to `_bundled_modules`, reading its `[tools.<key>]` config
from `mb.settings`. See `minibot/extensions/tools/utility.py`.

`ExtensionContext` (`app/extensions.py`) is deliberately narrow — this is the whole surface:

| Member | Notes |
|---|---|
| `mb.name` / `mb.config` / `mb.settings` | `config` is the `[extensions.config.<module>]` slice; `settings` is full validated `Settings` |
| `mb.event_bus` / `mb.logger` / `mb.entrypoint` | entrypoint is `"daemon" \| "console" \| "worker"` |
| `mb.vault` | unlocked vault or `None`; never expose a secret to the LLM |
| `mb.agent_registry` / `mb.agent_management` / `mb.refresh_tools` | registry is updated in place after registration; read specs when needed |
| `mb.on(EventType[, handler])` | decorator or direct call; extension subscriptions are **lossy** |
| `mb.tool(func)` | name = `__name__`, description = docstring, schema = first arg's pydantic model |
| `mb.add_tool(binding)` | escape hatch for a custom name, hand-written schema, or file-loaded description |
| `mb.add_service(service)` | anything with async `start()`/`stop()` |
| `mb.add_route` / `mb.add_prompt_fragment` / `mb.add_page` | HTTP routes, system-prompt fragments (optionally tied to tool names), web UI pages |

There is no `mb.container` and no DI accessors. Construct anything else inside `register()`
from `mb.settings`. `mb.tool` raises at registration (aborting startup) if the first argument
is not annotated with a **module-level** pydantic model, or if the function has no docstring.

### Core path (only what an extension cannot build)

Lean core (SKILL.md, `docs/architecture.md`): a tool goes here only when it needs state
`ExtensionContext` does not expose. If the gap is generic, propose widening `ExtensionContext`
instead.

```
Dispatcher._build_tools → build_enabled_tools()       app/tool_factory.py
    ChatMemoryTool      (always)                         needs: conversation memory backend
    SettingsInfoTool    (always)                         needs: config path, skill registry
    SkillLoaderTool     if tools.skills.enabled          needs: skill registry, managed storage
    SkillInstallerTool  if tools.skills.install          needs: skill registry
    AgentInfoTool       if the agent registry is non-empty   needs: LLM client factory
```

Create: `minibot/llm/tools/<module>.py` exposing `.bindings() -> list[ToolBinding]`, and
`minibot/llm/tools/<tool_name>.txt` for the description.
Edit: a config model + a field on `ToolsConfig` (`config/schema.py`), a branch in
`tool_factory.py`, and `config.example.toml`.

Use the `minibot-create-tool` skill for the file templates on either path.

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

- `__init__` subscribes outbound: `event_bus.subscribe(types=(OutboundEvent, OutboundFileEvent))`
- `async start()` — spawn the inbound poll task and the outbound drain task
- `async stop()` — close the subscription, cancel the tasks
- inbound: build `ChannelMessage(channel="<name>", ...)` (`core/channels.py`) and
  `await event_bus.publish(MessageEvent(message=...))`
- outbound: consume `OutboundEvent.response: ChannelResponse` and render `RenderableResponse`
- optional: handle `OutboundFormatRepairEvent` if the channel has a fragile markup parser

Create: `minibot/adapters/messaging/<name>/service.py`, `minibot/extensions/channels/<name>.py`.
Edit: the `daemon` branch of `_bundled_modules` (`app/extensions.py`) if bundling it;
otherwise just list the module under `[extensions].modules`.

Config: either a typed model plus a field on `ChannelsConfig` (`config/schema.py`), **or** the
escape hatch — `ChannelsConfig` is `extra="allow"` and `ChannelsConfig.section("<name>")`
returns the raw `[channels.<name>]` dict. The escape hatch is the intended
path for out-of-tree channels.

Optional prompt fragment: `prompts/channels/<name>.md` — see PROMPT_COMPOSITION.md.

Note the console channel is *not* an extension; `app/console.py` constructs `ConsoleService`
directly.

---

## 3. A new specialist agent

Pure file drop — **no Python edit**. Create `agents/<name>.md`.

```
AppContainer.configure()
  └─ load_agent_specs(orchestration.directory)   app/agent_definitions_loader.py
      ├─ sorted(root.glob("*.md"))
      ├─ split_frontmatter / parse_frontmatter    shared/frontmatter.py
      ├─ AgentDefinitionConfig.model_validate     config/schema.py
      └─ → AgentSpec                              core/agents.py
  └─ AgentRegistry(agent_specs)                   app/agent_registry.py
Dispatcher → build_enabled_tools(agent_registry=...) → AgentInfoTool → fetch_agent_info
Delegation itself is spawn_task, from the tasks extension:
  extensions/services/tasks.py → TaskTools → spawn_task → TaskManager → worker subprocess
PromptService._specialist_roster_fragment         app/handlers/services/prompt_service.py
```

Frontmatter is a hand-rolled YAML subset (scalars, 2-space lists, 2-space dicts) — not real
YAML. Fields are validated by `AgentDefinitionConfig`; `tools_allow` and `tools_deny` are
mutually exclusive. The Markdown body becomes the specialist system prompt and **must not be
empty** (that is fatal); a malformed name only warns.

Read the tool-scoping semantics in CONVENTIONS.md before setting `tools_allow` / `tools_deny` —
they do not mean what they appear to mean for MCP tools.

Agents are re-loaded independently by the worker and the task extensions, so an agent is
available to `spawn_task` without extra wiring.

---

## 4. A new config section

```
AppContainer.configure()
  └─ load_settings(path)                    adapters/config/loader.py
      ├─ resolve_config_path   (arg → $MINIBOT_CONFIG → ./config.toml)   config/paths.py
      └─ Settings.from_file → from_dict     config/schema.py
          ├─ expand_environment(...)        config/environment.py
          ├─ _normalize_for_annotation(...) config/schema.py
          └─ Settings.model_validate        (extra="forbid")
```

Create: nothing. Edit: a `BaseModel` in `config/schema.py` (keep the docstring
convention — `"""… TOML section: ``[x]``"""`), a field on `Settings` or on the relevant
parent (`ToolsConfig`, `ChannelsConfig`), then `config.example.toml` and
`docs/config.rst`. Add to `adapters/config/configurator.py` only if it belongs in
`minibot configure`.

Two things to know:

- **`Settings` is `extra="forbid"`** — an unknown top-level section is a hard boot failure.
  The two deliberate escape hatches that let you add config *without* touching the schema are
  `ChannelsConfig.section(...)` and `[extensions.config.<module>]`.
- **`${ENV}` expands in all string values before validation**, including extension config.
  Missing variables fail loading; `$${ENV}` escapes a literal reference. Preserve references
  when writing configuration. Existing `token_env` fields still resolve names in consuming
  code. See `docs/config.rst` for the full contract.

Byte-size fields use `ByteSizeValue`, which accepts strings like `"10MB"`.

---

## 5. A new event handler or dispatcher hook

`app/event_bus.py` is a bounded (`maxsize=128`) in-process async pub/sub. Core subscribers are
blocking and apply back-pressure; **extension subscribers are lossy and drop under pressure**.

Event catalog — `core/events.py`: `MessageEvent`, `OutboundEvent`, `OutboundFileEvent`,
`OutboundFormatRepairEvent`, `SystemEvent`, `TurnStartedEvent`, `TurnCompletedEvent`,
`TurnFailedEvent`, `ToolCallEvent`.

**(a) Observe an existing event — no core edit.**

```
register(mb) → @mb.on(TurnCompletedEvent)      app/extensions.py (ExtensionContext.on)
  └─ ExtensionRegistry.start()
      └─ event_bus.subscribe(types=(EventType,), lossy=True)
      └─ asyncio.create_task(...)   ← handler exceptions are logged and swallowed
```

Example: `examples/minibot_ext_demo.py`.

**(b) Emit a new event type.** Add the model to `core/events.py` (subclass `BaseEvent`, set
`event_type`), publish via `mb.event_bus.publish(...)`. Tool lifecycle events are emitted
automatically by the decorator in `llm/tools/tool_events.py`.

**(c) Handle a new event type in the Dispatcher** — `app/dispatcher.py`:

```
Dispatcher.__init__        widen event_bus.subscribe(types=(MessageEvent, OutboundFormatRepairEvent))
Dispatcher._run            add an isinstance branch
Dispatcher._handle_message / Dispatcher._handle_format_repair — add a sibling handler
```

**(d) A new turn-stage collaborator, not an event.** Add it to `app/handlers/services/`,
export it from that package's `__init__.py`, construct it in `build_llm_turn_service`
(`handlers/services/turn_service.py`), and pass it to `LLMTurnService`. The main path is
`Dispatcher._handle_message → LLMMessageHandler.handle → LLMTurnService.handle`.

---

## 6. Task workers

Workers are **forked subprocesses** with a deliberately narrower assembly than the daemon.

```
spawn_task (LLM tool)
  └─ producer.enqueue(TaskRequest)         core/tasks.py  (TaskProducer protocol)
      ├─ SQLiteTaskProducer                adapters/tasks/sqlite_store.py
      └─ RabbitMQTaskProducer              adapters/messaging/rabbitmq/producer.py
  └─ consumer leases the row
  └─ TaskManager  app/tasks/manager.py  → forks worker_entry over a pipe
      └─ worker_entry(pipe)                adapters/tasks/worker_process.py
          └─ worker_entry(pipe, backends)  app/tasks/worker.py
              └─ run_agent_loop(...)       app/tasks/worker.py
                  ├─ load_settings()
                  ├─ load_extensions(..., entrypoint="worker")   ← bundled modules: scheduler + utility
                  ├─ _resolve_task_spec(...)
                  ├─ _build_worker_tools(...)  app/tasks/worker.py
                  └─ AgentRuntime(...)
```

Two traps:

- **Workers load only the scheduler and utility bundles** (`_bundled_modules`,
  `app/extensions.py`). Besides them, only user-configured `[extensions].modules` load there,
  and bundled tool modules self-narrow when `mb.entrypoint == "worker"` (e.g. `read_docs`).
  To give workers a bundled extension tool, add its module to the worker branch.
- **Workers build their core tools independently of `tool_factory.py`.** To make a core tool
  worker-visible, add it to `_build_worker_tools` **and** `_WORKER_TOOL_ALLOWLIST`
  (both in `app/tasks/worker.py`). Extension tools are auto-appended and
  auto-allowlisted.

The default worker spec (`_build_worker_spec`) sets `max_tool_iterations=None`; steps and tool calls
are bounded by `[tasks].worker_max_steps` / `worker_max_tool_calls` instead.

For a new queue backend: implement `TaskProducer` (`core/tasks.py`) plus a consumer service,
add a backend literal to `TasksConfig` (`schema.py`), and add
`minibot/extensions/services/<backend>.py` to `_bundled_modules`, mirroring
`extensions/services/tasks.py` and `extensions/integrations/rabbitmq.py`.

---

## 7. A skill for MiniBot's own runtime

MiniBot implements the agentskills.io spec itself, so a skill in this repo is both a
development skill *and* something the bot can `activate_skill` at runtime.

```
AppContainer.configure()
  └─ SkillRegistry(paths=tools.skills.paths or None)   app/skill_registry.py
      ├─ resolve_skill_discovery_paths     app/skill_definitions_loader.py
      ├─ fingerprint_skill_paths           (path, mtime_ns, size)
      └─ load_skill_specs → parse_skill_file → SkillSpec   core/skills.py
Dispatcher → build_enabled_tools(skill_registry=...)
  └─ if tools.skills.enabled → SkillLoaderTool  app/tool_factory.py
PromptService._skill_catalog_fragment       app/handlers/services/prompt_service.py
runtime: every all()/get()/names() → SkillRegistry.refresh_if_stale()   app/skill_registry.py
```

Create: `<discovery-path>/<skill-name>/SKILL.md`. **No code change** — the registry hot-reloads
on an mtime/size fingerprint, unlike prompt files which are cached for the process lifetime.

Default discovery order (`_default_discovery_paths` in `app/skill_definitions_loader.py`):
`./.minibot/skills`, `./.agents/skills`, `~/.minibot/skills`, `~/.agents/skills`. In this repo `.claude/skills` is a
symlink to `.agents/skills`, so write new skills to `.agents/skills/`. Setting
`[tools.skills].paths` **replaces** the defaults entirely.

Frontmatter constraints are in CONVENTIONS.md — MiniBot's parser is stricter than the spec.
