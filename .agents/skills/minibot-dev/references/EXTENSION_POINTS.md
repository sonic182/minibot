# Extension Points

Where each kind of change lands, with the wiring chain that explains *why* it lands there.
Line numbers are anchors, not guarantees — confirm the symbol is still there before editing.

---

## 1. A new LLM tool

### Extension path (default)

```
daemon.run()                                   app/daemon.py
  └─ AppContainer.configure()                  adapters/container/app_container.py:37
      └─ load_extensions(...)                  app/extensions.py:197
          └─ importlib.import_module(name)     ← bundled list at extensions.py:26
              └─ your_module.register(mb)      ← YOUR CODE
                  └─ mb.tool / mb.add_tool
  └─ Dispatcher.__init__                       app/dispatcher.py:49
      └─ build_enabled_tools(extension_tools=...)   llm/tools/factory.py:28
          ├─ tools.extend(extension_tools)     factory.py:75
          └─ _ensure_unique_tool_names(tools)  factory.py:76
      └─ main_agent_tool_view(...)             app/tool_capabilities.py
```

Create: one module with `def register(mb: ExtensionContext) -> None`.
Edit: `[extensions].modules` in config, plus `[extensions.config.<module>]` for its settings.
Do **not** touch `schema.py` or `factory.py` on this path.

`ExtensionContext` (`app/extensions.py:56`) is deliberately narrow — this is the whole surface:

| Member | Anchor | Notes |
|---|---|---|
| `mb.name` / `mb.config` / `mb.settings` | `:66-68` | `config` is the `[extensions.config.<module>]` slice; `settings` is full validated `Settings` |
| `mb.event_bus` / `mb.logger` / `mb.entrypoint` | `:69-71` | entrypoint is `"daemon" \| "console" \| "worker"` |
| `mb.on(EventType[, handler])` | `:76` | decorator or direct call; extension subscriptions are **lossy** |
| `mb.tool(func)` | `:88` | name = `__name__`, description = docstring, schema = first arg's pydantic model |
| `mb.add_tool(binding)` | `:130` | escape hatch for a custom name, hand-written schema, or file-loaded description |
| `mb.add_service(service)` | `:136` | anything with async `start()`/`stop()` |

There is no `mb.container` and no DI accessors. Construct anything else inside `register()`
from `mb.settings`. `mb.tool` raises at registration (aborting startup) if the first argument
is not annotated with a **module-level** pydantic model, or if the function has no docstring.

### Core path (only for the shipped surface)

```
Dispatcher.__init__ → build_enabled_tools()    llm/tools/factory.py:28
    ChatMemoryTool        (always)             factory.py:47
    CalculatorTool        if tools.calculator.enabled     factory.py:51
    SkillLoaderTool       if tools.skills.enabled         factory.py:60
    AgentDelegateTool     if the agent registry is non-empty   factory.py:63
```

Create: `minibot/llm/tools/<module>.py` exposing `.bindings() -> list[ToolBinding]`, and
`minibot/llm/tools/<tool_name>.txt` for the description.
Edit: a config model + a field on `ToolsConfig` (`adapters/config/schema.py:667`), a branch in
`factory.py`, and `config.example.toml`.

Use the `minibot-create-tool` skill for the file templates on either path.

**Worker visibility is separate.** See §6 — a tool wired only into `factory.py` does not exist
for task workers.

---

## 2. A new messaging channel

There is **no channel registry**. A channel is a service class plus an extension that builds it.

```
AppContainer.configure(entrypoint="daemon")    adapters/container/app_container.py:37
  └─ load_extensions(...)                      app/extensions.py:197
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
Edit: the `daemon` branch of `_bundled_modules` (`app/extensions.py:26`) if bundling it;
otherwise just list the module under `[extensions].modules`.

Config: either a typed model plus a field on `ChannelsConfig` (`schema.py:136`), **or** the
escape hatch — `ChannelsConfig` is `extra="allow"` (`schema.py:145`) and `.section("<name>")`
(`schema.py:147`) returns the raw `[channels.<name>]` dict. The escape hatch is the intended
path for out-of-tree channels.

Optional prompt fragment: `prompts/channels/<name>.md` — see PROMPT_COMPOSITION.md.

Note the console channel is *not* an extension; it is constructed directly at
`app/console.py:59`.

---

## 3. A new specialist agent

Pure file drop — **no Python edit**. Create `agents/<name>.md`.

```
AppContainer.configure()
  └─ load_agent_specs(orchestration.directory)   app/agent_definitions_loader.py:18
      ├─ sorted(root.glob("*.md"))
      ├─ split_frontmatter / parse_frontmatter    shared/frontmatter.py
      ├─ AgentDefinitionConfig.model_validate     adapters/config/schema.py:318
      └─ → AgentSpec                              core/agents.py
  └─ AgentRegistry(agent_specs)                   app/agent_registry.py
Dispatcher → build_enabled_tools(agent_registry=...) → AgentDelegateTool
  → invoke_agent / fetch_agent_info
PromptService._specialist_roster_fragment         app/handlers/services/prompt_service.py:125
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
  └─ load_settings(path)                    adapters/config/loader.py:16
      ├─ resolve_config_path   (arg → $MINIBOT_CONFIG → ./config.toml)   loader.py:11
      └─ Settings.from_file → from_dict     adapters/config/schema.py
          ├─ _normalize_for_annotation(...) schema.py:49
          └─ Settings.model_validate        schema.py:818  (extra="forbid")
```

Create: nothing. Edit: a `BaseModel` in `adapters/config/schema.py` (keep the docstring
convention — `"""… TOML section: ``[x]``"""`), a field on `Settings` (`schema.py:818`) or on
the relevant parent (`ToolsConfig:667`, `ChannelsConfig:136`), then `config.example.toml` and
`docs/config.rst`. Add to `adapters/config/configurator.py` only if it belongs in
`minibot configure`.

Two things to know:

- **`Settings` is `extra="forbid"`** — an unknown top-level section is a hard boot failure.
  The two deliberate escape hatches that let you add config *without* touching the schema are
  `ChannelsConfig.section(...)` and `[extensions.config.<module>]`.
- **There is no `${ENV}` interpolation.** The loader is plain `tomllib`; the only environment
  variable read anywhere in config is `MINIBOT_CONFIG` (`loader.py:12`). The convention is to
  store the env var *name* in config (`token_env = "GITHUB_TOKEN"`) and call
  `os.environ.get(token_env)` in the consuming code.

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
register(mb) → @mb.on(TurnCompletedEvent)      app/extensions.py:76
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
dispatcher.py:51   widen event_bus.subscribe(types=(MessageEvent, OutboundFormatRepairEvent))
dispatcher.py:162  _run() — add an isinstance branch
dispatcher.py:181  _handle_message  /  :287 _handle_format_repair — add a sibling handler
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
  └─ TaskManager  adapters/tasks/manager.py  → forks worker_entry over a pipe
      └─ worker_entry(pipe)                adapters/tasks/worker.py:73
          └─ run_agent_loop(payload)
              ├─ load_settings()
              ├─ load_extensions(..., entrypoint="worker")   ← returns () for bundled modules
              ├─ _resolve_task_spec(...)
              ├─ _build_worker_tools(...)  worker.py:166
              └─ AgentRuntime(...)
```

Two traps:

- **Workers load no bundled extensions at all** (`app/extensions.py:26`, worker branch returns
  an empty tuple). Only user-configured `[extensions].modules` load there, and several bundled
  tool modules self-narrow when `mb.entrypoint == "worker"`.
- **Workers build tools independently of `factory.py`.** To make a core tool worker-visible,
  add it to `_build_worker_tools` (`worker.py:166`) **and** `_WORKER_TOOL_ALLOWLIST`
  (`worker.py:45`). Extension tools are auto-appended and auto-allowlisted.

`_WORKER_MAX_TOOL_ITERATIONS` is hard-coded to 8 (`worker.py:69`).

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
  └─ SkillRegistry(paths=tools.skills.paths or None)   app/skill_registry.py:14
      ├─ resolve_skill_discovery_paths     app/skill_definitions_loader.py:30
      ├─ fingerprint_skill_paths           (path, mtime_ns, size)
      └─ load_skill_specs → _parse_skill_file → SkillSpec   core/skills.py
Dispatcher → build_enabled_tools(skill_registry=...)
  └─ if tools.skills.enabled → SkillLoaderTool  llm/tools/factory.py:60
PromptService._skill_catalog_fragment       handlers/services/prompt_service.py:142
runtime: every all()/get()/names() → refresh_if_stale()   skill_registry.py:55
```

Create: `<discovery-path>/<skill-name>/SKILL.md`. **No code change** — the registry hot-reloads
on an mtime/size fingerprint, unlike prompt files which are cached for the process lifetime.

Default discovery order (`skill_definitions_loader.py:36`): `./.agents/skills`,
`./.claude/skills`, `~/.agents/skills`, `~/.claude/skills`. In this repo `.claude/skills` is a
symlink to `.agents/skills`, so write new skills to `.agents/skills/`. Setting
`[tools.skills].paths` **replaces** the defaults entirely.

Frontmatter constraints are in CONVENTIONS.md — MiniBot's parser is stricter than the spec.
