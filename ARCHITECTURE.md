# Architecture Overview

This document defines the initial product shape for the minibot daemon: an asyncio-based service that connects with Telegram (and future channels) and delegates requests to LLM providers compatible with `sonic182/llm-async`.

## Guiding Principles
- Favor clean layering with clear contracts between subsystems.
- Design for multi-channel, multi-provider expansion even if the first release only targets Telegram.
- Keep the runtime event driven (asyncio) and make every service injectable/testable.
- Use factories, composites, and singletons where they reduce coupling or duplication.

## Folder Layout (Lightweight Hexagonal)

We keep the package directly at repository root (`minibot/`) and slice code into lightweight hexagonal layers: `core` (domain), `app` (application services), and `adapters` (infrastructure). Cross-cutting utilities live in `shared/`.

```
.
├── ARCHITECTURE.md            # This document
├── README.md
├── pyproject.toml / poetry.lock
├── minibot/
│   ├── __init__.py
│   ├── app/                   # Application services + orchestration (hexagon edge)
│   │   ├── daemon.py          # Async entry point / lifecycle manager
│   │   ├── dispatcher.py      # Composite dispatcher for channel services
│   │   ├── handlers/
│   │   │   ├── __init__.py
│   │   │   ├── base.py        # Handler/middleware interfaces
│   │   │   └── llm_handler.py # Request→LLM→response pipeline
│   │   ├── scheduler_service.py # Coordinates scheduler adapter usage
│   │   └── use_cases/         # High-level workflows (e.g., reply_to_message)
│   ├── core/                  # Domain contracts & entities (hexagon center)
│   │   ├── __init__.py
│   │   ├── channels.py        # ChannelEvent, User, Conversation aggregates
│   │   ├── jobs.py           # Scheduler job entities/value objects
│   │   └── memory.py          # Memory abstractions (interfaces only)
│   ├── adapters/              # Infrastructure edges (driven/driving adapters)
│   │   ├── __init__.py
│   │   ├── config/
│   │   │   ├── loader.py      # Pydantic settings + .env integration
│   │   │   └── schema.py      # Typed config models (runtime, channels, llm)
│   │   ├── container/
│   │   │   └── app_container.py # Singleton service locator / DI helpers
│   │   ├── logging/
│   │   │   └── setup.py       # Structured logging configuration
│   │   ├── scheduler/
│   │   │   ├── jobs_mapper.py # Translators between domain jobs and adapter payloads
│   │   │   └── asyncio_scheduler.py # Async scheduler implementation
│   │   ├── messaging/
│   │   │   ├── factory.py     # Channel factory producing domain-facing services
│   │   │   ├── telegram/
│   │   │   │   └── service.py # Telegram connector (aiogram/PTB async)
│   │   │   └── protocols.py   # Adapter-side ChannelService protocol
│   │   ├── llm/
│   │   │   ├── provider_factory.py # Wraps sonic182/llm-async providers
│   │   │   ├── clients.py     # Concrete provider adapters
│   │   │   └── tools/         # Tool schemas + handlers surfaced to the LLM
│   │   ├── memory/
│   │   │   ├── in_memory.py   # Default backend
│   │   │   └── redis.py       # Production backend
│   │   └── persistence/
│   │       └── transcripts.py # Durable storage (optional)
│   └── shared/
│       ├── __init__.py
│       ├── utils.py
│       └── exceptions.py
└── tests/
    └── ... (mirrors minibot structure)
```

This layout avoids the `src/` indirection while retaining isolation between domain logic (`core`), application orchestration (`app`), and infrastructure adapters (`adapters`). Tests mirror the `minibot/` package. Infrastructure helpers (Dockerfiles, manifests) can live under `infra/` when needed.

## Scheduler Definition

Purpose: handle bot-triggered background work such as delayed replies, reminders, tool executions, or long-running LLM tasks without blocking the channel event loop.

- **Domain Interface** (`core/jobs.py` + `core.channels` callbacks): define `Job` entities with methods describing allowed transitions. Application services operate on this pure model.
- **Application Facade** (`app/scheduler_service.py`): exposes async methods `schedule(job)`, `cancel(job_id)`, `list_jobs(filter)`, `run_pending()`, `start()` / `stop()`. It orchestrates validation, authorization, and mapping to adapters.
- **Adapter Implementation** (`adapters/scheduler/asyncio_scheduler.py`): begins with an asyncio-friendly scheduler (custom priority queue loop or `apscheduler`). Jobs persist through the file-backed task storage (with `flock`-guarded writes) so restarts preserve schedules; adapters can later swap to SQLite/Redis without touching the application layer.
- **Integration**: handler pipelines enqueue jobs via the application facade; adapters emit completion events routed back to channel services so users receive results.
- **Extensibility**: additional adapters (Celery, Redis queue, cloud schedulers) can implement the same adapter protocol without changing domain/application layers.

## Runtime Loop

1. **Startup**: `minibot.app.daemon` loads config, builds the container singleton, configures logging, and creates the asyncio loop.
2. **Service Wiring**: the dispatcher resolves enabled channel adapters via the messaging factory, registers handler pipelines (LLM handler, scheduler facade, middleware), and connects them to the internal event bus.
3. **Main Loop**: `asyncio.gather` supervises channel services, the scheduler adapter, the event bus pump, and periodic maintenance tasks. Each component exposes `start()`/`stop()` for graceful shutdown.
4. **Event Flow**:
   - Channel adapter receives inbound message → maps to `ChannelEvent` and publishes it to the bus.
   - Dispatcher subscribes to inbound events, invokes the handler chain, and may synchronously respond or schedule deferred jobs.
   - Scheduler publishes completion events when jobs finish; dispatcher consumes them and emits user-facing replies.
   - Other subsystems (task service, memory hooks) can publish/subscribe to domain events without tight coupling.
5. **Shutdown**: signal handlers trigger `asyncio.TaskGroup` cancellation, ensuring bus consumers stop, channel connections close, scheduler persists outstanding jobs, and memory adapters flush state.

## Event Bus & Queue

- **Purpose**: decouple producers (channel adapters, scheduler completions, admin commands) from consumers (dispatcher, task service, monitoring). This keeps the "bot loop" centered on consuming events from one queue.
- **Implementation**: start with an in-process async queue abstraction (`app/event_bus.py`) backed by `asyncio.Queue` or `anyio` streams. Provide `publish(event)` and subscription helpers returning async iterators.
- **Boot Loop Behavior**: the daemon spins up a bus pump task that continuously consumes events and routes them to the dispatcher or specialized consumers. Flow control/backpressure is enforced via bounded queue sizes and per-consumer acknowledgements.
- **Extensibility**: replace the in-memory queue with Redis Streams, NATS, or RabbitMQ by implementing the same interface. Multiple consumers per topic can be supported via fan-out composites.
- **Observability**: expose queue depth, processing latency, and dropped-event counters; log events with `event_type`/`event_id` to trace cross-service flows.

## Future Interfaces & Control Planes

- **Messaging Ports**: the same channel abstraction can power future adapters beyond Telegram—Slack (Events API + Bolt RTM), Discord (gateway websockets), Matrix, generic webhooks, or even email/SMS relays. Each lives under `adapters/messaging/<channel>/` and plugs into the existing event bus.
- **HTTP/WebSocket API**: expose a control-plane server (FastAPI/Starlette) that surfaces REST/WebSocket endpoints for health checks, metrics, task CRUD, and manual message injection. This allows automation tools or dashboards to interact with the daemon without going through chat platforms.
- **Web Dashboard**: lightweight SPA (React/Svelte) or server-rendered UI for:
  - viewing live message streams and LLM responses
  - browsing/sorting tasks (pending, running, completed) and editing schedules
  - inspecting job history, memory entries, channel status, and config snapshots
  - triggering manual commands (pause channel, retry task, flush cache)
  The dashboard would consume the HTTP API and can be hosted separately or embedded in the daemon under `/admin`.
- **Programmatic Hooks**: provide outgoing webhooks or gRPC endpoints so external systems can subscribe to bot events (e.g., notify CI/CD). These reuse the event bus by adding dedicated subscribers that publish externally.

These interfaces are optional for MVP but the architecture keeps ports/adapters ready so they can be layered on without rewriting core logic.

## Memory Backend

The memory subsystem maintains conversation context, scheduler state, and ephemeral tool data.

- **Conversation History** (`core/memory.py`): `MemoryBackend` protocol exposes async `append_history` + `get_history` methods. Production deployments rely on `adapters/memory/sqlalchemy.py` (SQLite via `aiosqlite`) while lighter environments can swap to in-memory or Redis implementations.
- **Chat History Controls**: chat transcript storage and retention are configured under `[memory]` (for example `max_history_messages` to auto-trim old messages). LLM-exposed chat-memory management tools are always available as system tools and operate only on conversation history for the current session.
- **Key/Value Store** (`core/memory.py`): `KeyValueMemory` protocol powers durable “note taking” for LLM tools. The first adapter (`adapters/memory/kv_sqlalchemy.py`) stores entries with `owner_id`, `title`, `data`, metadata JSON, and lifecycle timestamps to enable fuzzy lookup and pagination.
- **Tool-Gated Access**: tool definitions under `minibot.llm.tools.*` wrap repository methods so the LLM must request reads/writes through declarative tool calls. Owner scoping is resolved server-side (using a configured `default_owner_id` or channel metadata), so prompts never expose tenant identifiers while still enforcing isolation.
- **HTTP Tooling**: optional HTTP client tools use `aiosonic` to fetch external URLs with strict method/timeout/output caps. Config toggles (`[tools.http_client]`) control availability per deployment.
- **Configuration Flags**: `[memory]` controls transcript persistence and optional retention limits, while `[tools.kv_memory]` toggles the SQLAlchemy KV store with pool/query limits. Chat-memory management tools remain always enabled as part of the core toolset.
- **Usage**: application services depend on the abstract interfaces; channel handlers read/write context, future schedulers persist job definitions, and LLM calls retrieve context windows. Adapters are resolved via the container so environments can swap backends without code changes.

## Task System & Strategy

- **Goal**: allow the bot to queue and manage short-lived “tasks” (commands, tool invocations, workflows) that may run synchronously or asynchronously.
- **Strategy Pattern**: introduce `TaskStrategy` objects describing how to execute a specific task type (LLM prompt, document lookup, tool call). The scheduler merely orchestrates timing; task strategies handle execution semantics.
- **Adapters**: `adapters/tasks/` hosts implementations such as `llm_prompt.py`, `web_fetch.py`, etc. Each implements `execute(payload, context)` returning status + result.
- **Application Layer**: `app/tasks_service.py` maps incoming commands to strategies, validates user permissions, and manages lifecycle (queued → running → completed/failed). It supports enqueueing tasks immediately or with a delay/cron-like schedule via the scheduler facade.
- **Persistence & History**: tasks maintain audit trails so the bot can list past tasks, inspect running tasks, and review future schedules. Application APIs expose CRUD (create/list/update/delete) plus query filters (status, owner, schedule window).
- **Silent completions**: not every job needs a user-facing reply; the dispatcher must tolerate completion events that update state/logs only.
- **Roadmap**:
  1. **Phase 1 (MVP)**: implement synchronous LLM request handler without formal task abstraction; responses are immediate.
  2. **Phase 2**: add scheduler integration for deferred tasks (reminders, long LLM jobs). Core job model + scheduler facade become stable.
  3. **Phase 3**: ship the task system with persistence (see below), enabling enqueue/delay/recurrent scheduling, history queries, and CRUD operations.
  4. **Phase 4**: extend to external tools (file generation, API calls), multi-step workflows, per-user quotas, and richer UIs.

### Task Persistence Options

- **Initial Implementation**: file-backed store (`adapters/tasks/storage/file_store.py`) that keeps JSON/MsgPack records on disk plus a lightweight index. Use POSIX `fcntl.flock` (or `portalocker` cross-platform shim) around read/write sections to guarantee atomic updates while keeping dependencies minimal. Store a write-ahead log so crash recovery can rebuild the index.
- **Async API**: exposed via the memory backend interface or dedicated `TaskRepository` protocol to allow listing finished tasks, running tasks, and future schedules. File access happens in executor threads to avoid blocking the event loop while the lock is held.
- **Upgrade Path**: once scale requires richer querying, swap in the SQLite adapter (via `aiosqlite`/SQLAlchemy) without changing application code. Future adapters include Redis (sorted sets for scheduling) or RabbitMQ/other queues for distributed workers; Postgres/MySQL support arrives by changing the SQLAlchemy URL.
- **Bot Tooling**: tasks are exposed as bot-accessible tools (e.g., “/tasks list”, “/tasks schedule daily_reminder”) so users can create/edit/delete/review tasks via chat commands or natural language.

Explicitly staging the task system keeps the MVP focused while leaving hooks (handlers can call `tasks_service` later) so future phases integrate without refactoring existing loops.

## Configuration Example (.toml)

Example runtime configuration (`config.toml`) demonstrating channels, LLM providers, scheduler, and memory settings:

```toml
[runtime]
log_level = "INFO"
environment = "development"

[channels.telegram]
enabled = true
bot_token = "123456:ABCDEF-your-token"
allowed_chat_ids = [123456789]
allowed_user_ids = [123456789, 987654321]
mode = "long_polling"          # or "webhook"
webhook_url = "https://example.com/webhook" # only if mode = "webhook"

[channels.slack]
enabled = false                 # placeholder for future adapters
bot_token = ""
signing_secret = ""
allowed_user_ids = []

[llm]
provider = "openai"            # must match sonic182/llm-async provider id
api_key = "sk-..."
model = "gpt-4o-mini"
max_new_tokens = 512
temperature = 0.4

[scheduler]
enabled = true
type = "asyncio"               # future: "redis", "celery"
poll_interval_ms = 500
default_retry_limit = 3

[memory]
backend = "in_memory"          # or "redis"

[memory.redis]
url = "redis://localhost:6379/0"
password = ""
ttl_seconds = 3600

[tools.kv_memory]
enabled = false
sqlite_url = "sqlite+aiosqlite:///./data/kv_memory.db"
pool_size = 5
echo = false
default_limit = 20
max_limit = 100
default_owner_id = "primary"

[tasks.storage]
backend = "file"
file_path = "./data/tasks.log"
lock_timeout_ms = 2000

[tasks.storage.sqlite]
url = "sqlite+aiosqlite:///./data/tasks.db"
echo = false
pool_size = 5

[memory.sqlalchemy]
enabled = false
url = "sqlite+aiosqlite:///./minibot.db"
pool_size = 5
echo = false

[memory.mongo]
enabled = false
url = "mongodb://localhost:27017"
database = "minibot"

[logging]
structured = true
json = true
service_name = "minibot"

[logging.logfmt]
enabled = true
kv_separator = "="
record_separator = " "

[llm.context]
history_window = 6              # number of past exchanges to send
system_prompt = "You are Minibot, a helpful assistant."
```

Secrets should be stored via environment variables or secret managers; TOML files can reference them using placeholder values or `${ENV_VAR}` syntax resolved by the config loader.

## Logging Strategy

- **Formatter**: use [`python-logfmter`](https://github.com/josheppinette/python-logfmter) to emit logfmt-formatted structured logs, which play nicely with human-readable CLIs and log aggregation systems.
- **Configuration**: `adapters/logging/setup.py` reads the `[logging]` section from config. If `logging.logfmt.enabled` is true, it configures `logging.Formatter` via `python_logfmter.Logfmter` using the separators from config; otherwise falls back to JSON or standard formatters.
- **Context Enrichment**: inject fields such as `service=minibot`, `component=telegram_adapter`, `request_id`, `user_id`, and scheduler job IDs. Use logging adapters or `structlog`-like processors to keep context consistent.
- **Levels/Handlers**: default to INFO; allow TRACE/DEBUG for development. Console handler is sufficient initially, but hooks exist for file or syslog handlers as needed.
- **Correlation IDs**: per incoming event, generate or reuse an ID stored in `contextvars` so downstream logs share the same `request_id` key in logfmt output.

## Next Steps

1. Scaffold the `src/minibot` package following the directories above.
2. Implement the config + container layers to make the rest of the services injectable.
3. Flesh out scheduler and memory contracts with accompanying unit tests.
4. Add the Telegram channel service and wire the handler pipeline + LLM provider integration.
