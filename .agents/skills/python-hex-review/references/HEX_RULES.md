# MiniBot Mini Hex Rules

## Canonical layer map

- `minibot.core`: domain contracts and ports — entities, value objects, events, channel
  abstractions and `ChannelCapabilities`, agent runtime contracts, and protocols for memory,
  files, tasks (`core/tasks.py`), skills, vectors, graph, decisions, secrets and MCP clients.
- `minibot.config`: the `Settings` schema, `${ENV}` / `${secret:}` expansion and config-path
  resolution. A shared configuration contract, importable from any layer.
- `minibot.app`: orchestration, dispatcher flow, runtime policy, handler coordination, tool
  assembly and visibility rules, task orchestration (`app.tasks`), environment context,
  response parsing.
- `minibot.adapters`: concrete implementations — config loader and configurator, container
  wiring, logging, messaging (Telegram, console, web, RabbitMQ), memory persistence, file
  storage, scheduler persistence, task stores, HTTP UI, Qdrant, vault, MCP clients.
- `minibot.llm`: provider factory, request building, schema handling, tool execution, tool
  definitions, provider registry, usage parsing.
- `minibot.rag`: chunking, embedding, reranking and retrieval over the `VectorStore` port
  (`core/vectors.py`); the Qdrant implementation lives in `adapters/qdrant/`.
- `minibot.app.extensions`: extension API, loading, and registry lifecycle.
- `minibot.extensions`: bundled extension entry points; each is thin composition over adapter
  implementations.
- `minibot.shared`: low-level reusable helpers that do not pull in channel, persistence,
  provider or terminal-presentation policy.

## Dependency direction

Current direction, checked against the imports:

- `core` imports no other `minibot` package.
- `llm` may import `core`, `config`, `shared` and `rag` (the RAG tools); it imports neither
  `app` nor `adapters`.
- `app` may import `core`, `config`, `shared` and `llm` (`app` → `llm`, never the reverse).
- `rag` imports `core` ports only — no adapters, channels or providers.
- `adapters` may import `core`, `config`, `shared`, `app` contracts (e.g. `EventBus`) and `llm`
  where they bootstrap a provider; `app` must not import adapters, except in the composition
  roots.
- Composition roots — `app/daemon.py`, `app/console.py`, `adapters/container/`,
  `adapters/tasks/worker_process.py` — may touch everything.
- Extensions may consume `ExtensionContext`, core event/DTO types, and the adapter
  implementation they compose; they must not reach into `AppContainer`.

Flag as violations:

- `core` importing from `minibot.app`, `minibot.adapters`, `minibot.llm`, or provider-specific modules.
- `llm` importing from `minibot.app` or `minibot.adapters`.
- `app` importing concrete storage, Telegram transport, or provider SDK details outside a
  composition root.
- handlers under `minibot.app.handlers` embedding business or persistence policy that belongs
  in a service, adapter, or contract.
- `adapters` returning framework- or provider-native payloads deep into the system without mapping.
- `shared` accumulating mixed policy from handlers, persistence, and providers.
- a bundled extension growing transport, persistence, or application policy instead of
  composing the existing adapter/service.
- an extension importing `AppContainer` or making its private wiring a de facto public API.

## Layer ownership details

### Core

Allowed:
- dataclasses, pydantic models, protocols, enums, domain validation
- abstract event, channel, agent, memory, job, task, skill, vector or MCP contracts

Disallowed:
- Telegram or console transport types
- SQLAlchemy sessions or ORM models
- LLM provider request or response payload shapes
- filesystem, network, MCP, or logging setup details

### App

Allowed:
- dispatcher and event-bus flow
- request orchestration across runtime services
- tool assembly, tool policy decisions and delegation flow
- task orchestration and the worker loop
- response parsing and environment context assembly

Disallowed:
- raw DB queries
- direct provider HTTP payload shaping when `minibot.llm` owns it
- Telegram-specific parsing beyond what a channel adapter should hand off

### Adapters

Allowed:
- SQLAlchemy persistence implementations
- Telegram, console and web service implementations; channel capability constants
- config loading and the interactive configurator
- logging setup, file storage, MCP client transport

Disallowed:
- central business policy
- handler orchestration that belongs in `minibot.app`

### LLM

Allowed:
- provider selection and bootstrap
- schema policy, tool execution, request building, usage parsing
- tool implementations and provider-specific request translation

Disallowed:
- leaking provider-native payload dicts across unrelated layers
- hiding transport or persistence policy that should stay in `app` or `adapters`

### Extensions

Allowed:
- module-level `register(mb)` functions that select config and compose tools, subscriptions,
  services, routes, pages or prompt fragments
- bundled entry points under `minibot.extensions` that import their existing adapter implementation
- configuration through `mb.config`, read-only `mb.settings`, or `ChannelsConfig.section(name)`

Disallowed:
- importing `AppContainer` to acquire hidden backends or runtime services
- moving an adapter implementation into `minibot.extensions` just because it is bundled
- duplicating LLM tool execution, output handling, or lifecycle ownership already provided by
  the registry

Lifecycle rules:
- services are started and stopped only by `ExtensionRegistry`; do not create unmanaged
  background tasks in `register()`.
- a channel extension must return from `register()` before constructing a service when
  `mb.entrypoint` is not `"daemon"`.
- event handlers are lossy observers: keep them bounded, tolerate dropped events, and let
  their failures stay isolated from turns.
- extension import and registration failures are fatal; a runtime handler failure must be
  logged and must not kill its subscription.
- task workers load a narrower set of extensions (scheduler, utility, `[tasks]
  worker_extensions`, user modules), and worker registries never start services — a
  service-backed tool in a worker must start lazily. Details: `minibot-dev` skill,
  EXTENSION_POINTS.md §6.

### Shared

Allowed:
- generic parsing, datetime, retries, path, schema, and prompt-loading helpers

Disallowed:
- framework-aware helpers
- storage-aware helpers
- provider-aware orchestration
- terminal presentation code

## Async rules

- I/O-heavy paths remain async-first (`async def`); do not hide blocking work in handlers,
  tool execution, or provider calls.
- Blocking work must be isolated behind a dedicated adapter or executor boundary
  (e.g. `asyncio.to_thread`).
- Background tasks must be explicit and tied to lifecycle management.
- Timeout, cancellation, and retry policy should be coherent in one layer (orchestration or a
  dedicated helper) instead of scattered across handlers and adapters.
- Keep side effects explicit and close to the boundary that owns them.

## MiniBot-specific smells

- `minibot.app.handlers.*` calling concrete storage or provider clients directly.
- `minibot.core.*` importing config, logging, SQLAlchemy, Telegram, MCP, or LLM provider modules.
- tool modules in `minibot.llm.tools` carrying channel-specific response formatting.
- `minibot.adapters.messaging.*` deciding tool policy or delegation policy.
- `minibot.shared.utils` or similar helpers becoming a mixed-layer escape hatch.
- raw external payload dictionaries crossing multiple modules without a typed boundary model.
- a core mechanism acting on tools (visibility, approval, output spill, worker allowlist) that
  only handles core tools, not extension tools (*Lean core* in the `minibot-dev` skill).
