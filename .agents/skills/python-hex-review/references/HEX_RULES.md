# Minibot Mini Hex Rules

## Canonical layer map

- `minibot.core`: domain contracts, entities, value objects, events, channel abstractions, agent runtime contracts, memory contracts, job contracts.
- `minibot.app`: orchestration, dispatcher flow, runtime policy, handler coordination, environment context, tool visibility rules, response parsing.
- `minibot.adapters`: concrete implementations for config loading, container wiring, logging, messaging, memory persistence, file storage, scheduler persistence, MCP clients.
- `minibot.llm`: provider factory, request building, schema handling, tool execution, tool definitions, provider registry, usage parsing.
- `minibot.app.extensions`: extension API, loading, and registry lifecycle.
- `minibot.extensions`: bundled extension entry points; each is thin composition over adapter implementations.
- `minibot.shared`: low-level reusable helpers that do not pull in channel, persistence, or provider policy.

## Dependency direction

Prefer this direction:

- `core` depends on standard library and narrow third-party primitives only when they are not infrastructure-specific.
- `app` depends on `core`, selected `shared` helpers, and abstractions needed to orchestrate runtime flow.
- `adapters` depends on `core` and `app` contracts, never the other way around.
- `llm` may depend on `core`, selected `app` runtime contracts, and `shared` helpers when integrating providers and tools.
- Extensions may consume the public extension context, core event/DTO types, and the adapter implementation they compose; they must not reach into `AppContainer`.
- Entry points and container wiring may touch everything because they are the composition root.

Flag as violations:

- `core` importing from `minibot.app`, `minibot.adapters`, or provider-specific modules.
- `app` importing concrete storage, Telegram transport, or provider SDK details for anything beyond wiring.
- handlers under `minibot.app.handlers` embedding business or persistence policy that belongs in a service, adapter, or contract.
- `adapters` returning framework- or provider-native payloads deep into the system without mapping.
- `shared` accumulating mixed policy from handlers, persistence, and providers.
- a bundled extension growing transport, persistence, or application policy instead of composing the existing adapter/service.
- an extension importing `AppContainer` or making its private wiring a de facto public API.

## Layer ownership details

### Core

Allowed:
- dataclasses, pydantic models, protocols, enums, domain validation
- abstract event, channel, agent, memory, or job contracts

Disallowed:
- Telegram or console transport types
- SQLAlchemy sessions or ORM models
- LLM provider request or response payload shapes
- filesystem, network, MCP, or logging setup details

### App

Allowed:
- dispatcher and event-bus flow
- request orchestration across runtime services
- tool policy decisions and delegation flow
- response parsing and environment context assembly

Disallowed:
- raw DB queries
- direct provider HTTP payload shaping when `minibot.llm` owns it
- Telegram-specific parsing beyond what a channel adapter should hand off

### Adapters

Allowed:
- SQLAlchemy persistence implementations
- Telegram and console service implementations
- config loading and schema materialization
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
- module-level `register(mb)` functions that select config and compose tools, subscriptions, or services
- bundled entry points under `minibot.extensions` that import their existing adapter implementation
- extension-owned configuration only through `mb.config` or its named `[channels.<name>]` section

Disallowed:
- importing `AppContainer` to acquire hidden backends or runtime services
- moving an adapter implementation into `minibot.extensions` just because it is bundled
- duplicating LLM tool execution, output handling, or lifecycle ownership already provided by the registry

Lifecycle rules:
- services are started and stopped only by `ExtensionRegistry`; do not create unmanaged background tasks in `register()`.
- a channel extension must return from `register()` before constructing a service when `mb.entrypoint` is not `"daemon"`.
- event handlers are lossy observers: keep them bounded, tolerate dropped events, and let their failures stay isolated from turns.
- extension import and registration failures are fatal; a runtime handler failure must be logged and must not kill its subscription.
- tools are available to the main and delegated agents, but not task workers until worker extension loading is implemented.

### Shared

Allowed:
- generic parsing, datetime, retries, path, schema, and prompt-loading helpers

Disallowed:
- framework-aware helpers
- storage-aware helpers
- provider-aware orchestration

## Async rules

- I/O-heavy paths remain async-first.
- Blocking work must be isolated behind a dedicated adapter or executor boundary.
- Background tasks must be explicit and tied to lifecycle management.
- Timeout, cancellation, and retry policy should be coherent in one layer instead of scattered across handlers and adapters.

## Minibot-specific smells

- `minibot.app.handlers.*` calling concrete storage or provider clients directly.
- `minibot.core.*` importing config, logging, SQLAlchemy, Telegram, MCP, or LLM provider modules.
- tool modules in `minibot.llm.tools` carrying channel-specific response formatting.
- `minibot.adapters.messaging.*` deciding tool policy or delegation policy.
- `minibot.shared.utils` or similar helpers becoming a mixed-layer escape hatch.
- raw external payload dictionaries crossing multiple modules without a typed boundary model.
