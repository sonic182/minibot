# Architecture Overview

This document describes the architecture that exists in the current repository.

MiniBot is an asyncio daemon that receives Telegram updates, publishes inbound events to an internal event bus,
routes messages through the LLM pipeline, and emits outbound responses back to the channel adapter.

## Guiding Principles

- Keep a lightweight hexagonal split: `core` (domain contracts), `app` (orchestration), `adapters` (infrastructure), `llm` (provider/tool integration).
- Prefer async-first boundaries for I/O-heavy paths (Telegram, DB, provider calls).
- Keep infrastructure replaceable behind protocols (memory repositories, scheduled prompt store, tools).
- Maintain explicit, testable flow with dependency wiring centralized in the container.

## Repository Layout (Current)

```text
.
├── ARCHITECTURE.md
├── README.md
├── TODO.md
├── config.example.toml
├── prompts/
│   └── channels/
│       └── telegram.md
├── Dockerfile
├── docker-compose.yml
├── minibot/
│   ├── app/
│   │   ├── agent_runtime.py
│   │   ├── daemon.py
│   │   ├── dispatcher.py
│   │   ├── event_bus.py
│   │   ├── scheduler_service.py
│   │   └── handlers/
│   │       └── llm_handler.py
│   ├── core/
│   │   ├── channels.py
│   │   ├── events.py
│   │   ├── jobs.py
│   │   └── memory.py
│   ├── adapters/
│   │   ├── config/
│   │   │   ├── loader.py
│   │   │   └── schema.py
│   │   ├── container/
│   │   │   └── app_container.py
│   │   ├── logging/
│   │   │   └── setup.py
│   │   ├── memory/
│   │   │   ├── sqlalchemy.py
│   │   │   └── kv_sqlalchemy.py
│   │   ├── messaging/
│   │   │   └── telegram/
│   │   │       └── service.py
│   │   └── scheduler/
│   │       └── sqlalchemy_prompt_store.py
│   ├── llm/
│   │   ├── provider_factory.py
│   │   └── tools/
│   │       ├── base.py
│   │       ├── factory.py
│   │       ├── calculator.py
│   │       ├── chat_memory.py
│   │       ├── http_client.py
│   │       ├── kv.py
│   │       ├── python_exec.py
│   │       ├── scheduler.py
│   │       └── time.py
│   └── shared/
│       ├── prompt_loader.py
│       └── utils.py
└── tests/
    └── ... (mirrors runtime modules)
```

## Runtime Flow

1. `minibot.app.daemon` boots settings, logging, memory, tool bindings, Telegram service, dispatcher, and scheduled prompt service.
2. Telegram adapter receives inbound updates and maps them into `ChannelMessage` payloads.
3. Adapter publishes `MessageEvent` into `app.event_bus.EventBus`.
4. `app.dispatcher.Dispatcher` consumes `MessageEvent` and invokes `LLMMessageHandler`.
5. Handler loads conversation history, composes an effective system prompt (`llm.system_prompt` + optional channel prompt from `llm.prompts_dir/channels/<channel>.md`), executes directive-aware tool-capable generation through `app.agent_runtime.AgentRuntime`, and returns `ChannelResponse`.
6. Dispatcher publishes `OutboundEvent` unless `metadata.should_reply` is false.
7. Telegram adapter consumes outbound responses and sends text to Telegram (with chunking for long messages).

This design keeps channel I/O, model orchestration, and persistence decoupled while preserving a single async event spine.

## Core Domain Contracts

- `core/channels.py`: inbound/outbound DTOs (`ChannelMessage`, `ChannelResponse`) and message metadata; includes attachment payloads for multimodal inputs.
- `core/events.py`: event types (`MessageEvent`, `OutboundEvent`, base event envelope).
- `core/memory.py`: transcript and KV memory protocols.
- `core/jobs.py`: scheduled prompt entities, status enums, recurrence model, and repository protocol.

## Application Layer

- `app/event_bus.py`: in-process async pub/sub over `asyncio.Queue` with subscription iterators.
- `app/dispatcher.py`: main event consumer; invokes handler pipeline and controls reply suppression through metadata.
- `app/handlers/llm_handler.py`:
  - assembles model input from text plus attachments,
  - composes per-channel system prompt by loading prompt fragments from `shared/prompt_loader.py`,
  - enforces provider constraints for multimodal support,
  - stores transcript history safely (attachment summaries only, not raw blobs),
  - parses structured model output (`answer`, `should_answer_to_user`).
- `app/agent_runtime.py`:
  - owns directive-loop execution (`provider step -> tool calls -> tool output append -> directive apply -> next step`),
  - maintains runtime `AgentState` (`messages`, `meta`),
  - renders managed-file directive parts into provider multimodal payloads,
  - enforces loop limits (`max_steps`, `max_tool_calls`, timeout) and directive trust policy.
- `app/scheduler_service.py`: scheduled prompt orchestration (`schedule`, `list`, `cancel`, `delete`, polling loop, retry/recurrence handling, event publishing).

## Infrastructure Adapters

- Config:
  - `adapters/config/schema.py` holds Pydantic settings models.
  - `adapters/config/loader.py` resolves TOML + environment placeholders.
- Container:
  - `adapters/container/app_container.py` wires singleton-style service graph.
- Logging:
  - `adapters/logging/setup.py` configures structured logfmt-friendly logging.
- Messaging:
  - `adapters/messaging/telegram/service.py` handles Telegram authorization, inbound text/media extraction, outbound message sending, and long-message chunking.
- Files:
  - `adapters/files/local_storage.py` handles managed workspace path-safe list/write/read operations.
- Memory:
  - `adapters/memory/sqlalchemy.py` persists chat history.
  - `adapters/memory/kv_sqlalchemy.py` persists KV tool memory.
- Scheduler persistence:
  - `adapters/scheduler/sqlalchemy_prompt_store.py` stores scheduled prompts in SQLite via SQLAlchemy.

## LLM Layer

- `llm/provider_factory.py`: provider/client abstraction around `sonic182/llm-async`, including tool execution loops and provider capability branching.
- `llm/tools/factory.py`: builds enabled tool bindings from settings.
- `llm/tools/*`: concrete tool schemas + handlers:
  - chat memory management,
  - calculator,
  - HTTP client,
  - KV memory,
  - Python execution,
  - file tools (`list_files`, `create_file`, `send_file`, `self_insert_artifact`),
  - scheduler controls (`schedule_prompt`, `cancel_scheduled_prompt`, `list_scheduled_prompts`, `delete_scheduled_prompt`),
  - time helpers.

## Scheduler Model (Current)

The scheduler currently focuses on scheduled prompts (not a generic task DAG engine).

- Jobs are persisted in SQLite (`scheduled_prompts` table).
- Service leases due jobs, dispatches them through the event bus, retries failures with backoff, and supports interval recurrence.
- Scope checks enforce owner/channel/chat/user constraints for cancel/delete/list operations.
- Deletion is explicit user-triggered behavior; active jobs are cancelled before hard delete.

## Multimodal Input Path (Telegram -> LLM)

- Telegram adapter can ingest `photo` and `document` updates when media is enabled in config.
- Attachments are normalized into Responses-style parts:
  - images -> `input_image` (data URL),
  - non-image docs -> `input_file`.
- Handler sends multimodal content only when provider mode supports it (`openai_responses` path).
- For non-supporting providers, handler returns a clear user-facing message and avoids invalid provider calls.

## Data and State

- Conversation history: SQLite transcript store (optional max-history trimming).
- KV notes: optional SQLAlchemy-backed store under tool controls.
- Scheduled prompts: SQLite prompt store with recurrence + retry metadata.
- Runtime queue state: in-process, ephemeral, reconstructed on restart from durable stores.

## Configuration Surface

`config.example.toml` is the canonical reference (with inline notes for production-oriented values).

Main sections:

- `[runtime]`
- `[channels.telegram]` (auth allowlists, mode, media limits)
- `[llm]`
  - `llm.prompts_dir` points to channel prompt packs (default `./prompts`)
- `[memory]`
- `[scheduler.prompts]`
- `[tools.*]` (`kv_memory`, `http_client`, `calculator`, `python_exec`, `time`)
- `[logging]`

## Testing Strategy

- Unit and integration-style tests under `tests/` mirror runtime modules.
- Coverage focuses on config loading, event bus behavior, handler/tool flows, scheduler persistence/service behavior, provider interface, and Telegram adapter authorization/media mapping.
- Async paths are validated with `pytest` + `pytest-asyncio`.

## Current Boundaries and Future Extensions

Current architecture supports:

- one active channel adapter (Telegram),
- one daemon process with in-process event bus,
- SQLite-backed persistence for memory and scheduled prompts,
- tool-augmented LLM interactions.

Natural extension points already in place:

- add new messaging adapters under `adapters/messaging/`,
- add alternative persistence adapters behind existing protocols,
- add richer control-plane interfaces (HTTP/WebSocket) without rewriting core dispatch flow,
- evolve scheduled prompts into broader task orchestration while preserving event bus contracts.
│   │   ├── files/
│   │   │   └── local_storage.py
