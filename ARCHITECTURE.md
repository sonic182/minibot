# Architecture Overview

This document describes the architecture that exists in the current repository.

MiniBot is an asyncio application with two runtime entrypoints:

- daemon mode (`minibot.app.daemon`) for Telegram,
- interactive CLI mode (`minibot.app.console`) for local console conversations.

Both entrypoints publish inbound events to an internal event bus, route messages through the LLM pipeline,
and emit outbound responses back to the active channel adapter.

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
├── agents/              (specialist agent definition markdown files)
├── config.example.toml
├── config.yolo.toml
├── docker-requirements.txt
├── prompts/
│   ├── channels/
│   │   ├── console.md
│   │   └── telegram.md
│   ├── compact.md
│   ├── main_agent_system.md
│   └── policies/
│       ├── delegation.md
│       └── tool_usage.md
├── Dockerfile
├── docker-compose.yml
├── minibot/
│   ├── app/
│   │   ├── agent_definitions_loader.py
│   │   ├── agent_policies.py
│   │   ├── agent_registry.py
│   │   ├── agent_runtime.py
│   │   ├── console.py
│   │   ├── daemon.py
│   │   ├── delegation_trace.py
│   │   ├── dispatcher.py
│   │   ├── environment_context.py
│   │   ├── event_bus.py
│   │   ├── incoming_files_context.py
│   │   ├── llm_client_factory.py
│   │   ├── mcp_tool_name.py
│   │   ├── response_parser.py
│   │   ├── runtime_limits.py
│   │   ├── runtime_structured_output.py
│   │   ├── scheduler_service.py
│   │   ├── tool_capabilities.py
│   │   ├── tool_guardrail_validator.py
│   │   ├── tool_policy_utils.py
│   │   ├── tool_use_guardrail.py
│   │   └── handlers/
│   │       ├── llm_handler.py
│   │       └── services/
│   │           ├── audio_transcription_service.py
│   │           ├── compaction_service.py
│   │           ├── input_service.py
│   │           ├── metadata_service.py
│   │           ├── prompt_service.py
│   │           ├── runtime_service.py
│   │           ├── session_state_service.py
│   │           └── tool_audio_executor.py
│   ├── core/
│   │   ├── agent_runtime.py
│   │   ├── agents.py
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
│   │   ├── sqlalchemy_utils.py
│   │   ├── logging/
│   │   │   └── setup.py
│   │   ├── mcp/
│   │   │   └── client.py
│   │   ├── files/
│   │   │   └── local_storage.py
│   │   ├── memory/
│   │   │   ├── sqlalchemy.py
│   │   │   └── kv_sqlalchemy.py
│   │   ├── messaging/
│   │   │   ├── console/
│   │   │   │   └── service.py
│   │   │   └── telegram/
│   │   │       ├── incoming_media_mapper.py
│   │   │       └── service.py
│   │   └── scheduler/
│   │       └── sqlalchemy_prompt_store.py
│   ├── llm/
│   │   ├── provider_factory.py
│   │   ├── services/
│   │   │   ├── client_bootstrap.py
│   │   │   ├── compaction.py
│   │   │   ├── models.py
│   │   │   ├── provider_registry.py
│   │   │   ├── request_builder.py
│   │   │   ├── schema_fallback.py
│   │   │   ├── schema_policy.py
│   │   │   ├── tool_executor.py
│   │   │   ├── tool_loop_guard.py
│   │   │   └── usage_parser.py
│   │   └── tools/
│   │       ├── descriptions/      (tool description .txt files, loaded at runtime)
│   │       ├── agent_delegate.py
│   │       ├── arg_utils.py
│   │       ├── audio_transcription.py
│   │       ├── audio_transcription_facade.py
│   │       ├── apply_patch.py
│   │       ├── bash.py
│   │       ├── base.py
│   │       ├── calculator.py
│   │       ├── chat_memory.py
│   │       ├── description_loader.py
│   │       ├── factory.py
│   │       ├── file_storage.py
│   │       ├── grep.py
│   │       ├── http_client.py
│   │       ├── mcp_bridge.py
│   │       ├── python_exec.py
│   │       ├── patch_engine.py
│   │       ├── schema_utils.py
│   │       ├── scheduler.py
│   │       ├── time.py
│   │       └── user_memory.py
│   └── shared/
│       ├── assistant_response.py
│       ├── console_compat.py
│       ├── datetime_utils.py
│       ├── json_schema.py
│       ├── parse_utils.py
│       ├── path_utils.py
│       ├── prompt_loader.py
│       ├── retries.py
│       └── utils.py
└── tests/
    └── ... (mirrors runtime modules)
```

## Runtime Flow

1. Entry point (`minibot.app.daemon` or `minibot.app.console`) boots settings, logging, memory, tools, and dispatcher.
2. Channel adapter (`TelegramService` or `ConsoleService`) maps input into `ChannelMessage` and publishes `MessageEvent`.
3. `MessageEvent` is published into `app.event_bus.EventBus`.
4. `app.dispatcher.Dispatcher` consumes `MessageEvent` and invokes `LLMMessageHandler`.
5. `Dispatcher` builds main-agent tool visibility first (`app.tool_capabilities.main_agent_tool_view`):
   - optional main-agent allow/deny tool policy,
   - optional exclusive ownership mode that hides agent-owned tools from the main agent.
6. Handler loads history, composes system prompt fragments, and builds tool context.
7. Main agent (`minibot`) runs in `AgentRuntime` with full tool loop.
8. Delegation is tool-driven:
   - main agent may call `invoke_agent`,
   - `invoke_agent` resolves specialist by name, applies agent tool policy, and runs specialist runtime with ephemeral in-turn state,
   - result returns to main agent as tool output, then main agent composes final answer.
9. Handler returns `ChannelResponse` with metadata (`primary_agent`, optional `agent_trace`, `delegation_fallback_used`, token trace).
10. Dispatcher publishes `OutboundEvent` unless `metadata.should_reply` is false.
11. Active channel adapter consumes outbound response and renders it to user (Telegram send or console print).

This design keeps channel I/O, model orchestration, and persistence decoupled while preserving a single async event spine.

## Console Agent Invocation Flow (Example)

Example request and agent invocation flow when using `minibot-console`.

```mermaid
flowchart TD
    U[User in terminal] --> C[minibot-console]
    C --> CS[ConsoleService.publish_user_message]
    CS --> EB[(EventBus)]
    EB --> D[Dispatcher]
    D --> STV[main_agent_tool_view]
    D --> H[LLMMessageHandler.handle]

    STV --> H
    H --> SUP

    SUP[Main Agent Runtime]
    DEC{Need specialist?}
    FINAL[Main agent final answer]
    IA[invoke_agent tool]

    subgraph MAIN[Main Agent minibot]
        SUP
        DEC
        FINAL
        IA
    end

    SUP --> DEC
    DEC -->|no| FINAL
    DEC -->|yes| IA

    IA --> AR[AgentRegistry lookup]

    S[Specialist AgentRuntime]
    TP[Apply per-agent tool policy; tools_allow/tools_deny/mcp_servers]
    T[Specialist tool calls + output]

    subgraph SPEC[Specialist Agent]
        S
        TP
        T
    end

    S --> TP
    TP --> T

    AR --> S
    T --> SUP
    FINAL --> H

    H --> RESP[ChannelResponse metadata: primary_agent, delegation_fallback_used, agent_trace]
    RESP --> D
    D --> EB2[(OutboundEvent)]
    EB2 --> C
    C --> U2[Rendered response in terminal]
```

## Core Domain Contracts

- `core/agents.py`: agent definitions (`AgentSpec`) and delegation payload (`DelegationDecision`).
- `core/agent_runtime.py`: runtime state/message/part model (`AgentState`, `AgentMessage`, `MessagePart`, limits/directives).
- `core/channels.py`: inbound/outbound DTOs (`ChannelMessage`, `ChannelResponse`) and message metadata; includes attachment payloads for multimodal inputs.
- `core/events.py`: event types (`MessageEvent`, `OutboundEvent`, base event envelope).
- `core/memory.py`: transcript and KV memory protocols.
- `core/jobs.py`: scheduled prompt entities, status enums, recurrence model, and repository protocol.

## Application Layer

- `app/event_bus.py`: in-process async pub/sub over `asyncio.Queue` with subscription iterators.
- `app/dispatcher.py`: main event consumer; builds enabled tools, applies main-agent tool visibility policy, invokes handler pipeline, controls reply suppression.
- `app/agent_definitions_loader.py`: loads specialist definitions from Markdown files with YAML-like frontmatter.
- `app/agent_registry.py`: in-memory registry for discovered `AgentSpec` entries.
- `app/agent_policies.py`: enforces per-agent tool scoping (`tools_allow`, `tools_deny`, MCP server allowlist).
- `app/tool_capabilities.py`: computes main-agent-visible tools and capability summaries for prompts/tool policies.
- `app/delegation_trace.py`: extracts delegation trace from runtime state (`DelegationTraceResult`, `extract_delegation_trace`, `count_tool_messages`).
- `app/environment_context.py`: builds the environment context fragment injected into system prompts (e.g. configured output directories).
- `app/incoming_files_context.py`: assembles incoming-file context for model input and history entries (`build_incoming_files_text`, `build_history_user_entry`, `incoming_files_from_metadata`).
- `app/mcp_tool_name.py`: utilities for parsing and validating MCP-namespaced tool names (`is_mcp_tool_name`, `extract_mcp_server`).
- `app/response_parser.py`: parses structured LLM output payloads into render objects (`extract_answer`, `render_from_payload`, `payload_to_object`, `plain_render`).
- `app/runtime_limits.py`: constructs `AgentRuntimeLimits` from config and client capabilities (`build_runtime_limits`).
- `app/runtime_structured_output.py`: ratchet-backed structured-output schema validation with retries/fallback payload shaping (`RuntimeStructuredOutputValidator`).
- `app/tool_guardrail_validator.py`: ratchet-backed structured-output validator for tool-use guardrail classification payloads.
- `app/tool_policy_utils.py`: shared `fnmatch`-based tool allow/deny filtering used by agent policies and tool capability views.
- `app/tool_use_guardrail.py`: `ToolUseGuardrail` protocol with `NoopToolUseGuardrail` (default) and ratchet-validated `LLMClassifierToolUseGuardrail` (opt-in via `[orchestration].main_tool_use_guardrail = "llm_classifier"`).
- `app/handlers/llm_handler.py`: top-level request flow coordinator for history persistence, runtime execution, format repair, and response metadata assembly.
- `app/handlers/services/*`: extracted collaborators used by `LLMMessageHandler`:
  - `audio_transcription_service.py`: short-audio candidate selection, auto-transcription execution, and prompt-prefix composition,
  - `input_service.py`: multimodal input shaping (`responses` vs chat-completions compatible parts),
  - `prompt_service.py`: base prompt + policy/channel fragments + environment/tool guidance composition,
  - `runtime_service.py`: main runtime execution, guardrail retry path, and delegation fallback handling,
  - `compaction_service.py`: token-pressure checks and compaction strategy (Responses compact endpoint or summary fallback),
  - `session_state_service.py`: per-session token counters and `previous_response_id` tracking,
  - `metadata_service.py`: provider/model metadata extraction for outbound responses,
  - `tool_audio_executor.py`: adapter for executing `transcribe_audio` tool bindings from app services.
- `app/agent_runtime.py`:
  - owns directive-loop execution (`provider step -> tool calls -> tool output append -> directive apply -> next step`),
  - maintains runtime `AgentState` (`messages`, `meta`),
  - renders managed-file directive parts into provider multimodal payloads,
  - enforces loop limits (`max_steps`, `max_tool_calls`, timeout) and directive trust policy.
- `app/llm_client_factory.py`: builds/caches default and per-agent LLM clients, resolving credentials from `[providers.<name>]`.
- `app/scheduler_service.py`: scheduled prompt orchestration (`schedule`, `list`, `cancel`, `delete`, polling loop, retry/recurrence handling, event publishing).
- `app/console.py`: interactive `minibot-console` runner on top of `ConsoleService` + dispatcher.

## Agent Architecture (Current)

- Agent definitions live in `agents/*.md`:
  - frontmatter describes identity/routing/runtime policy (`name`, `description`, `model_provider`, `model`, `max_tool_iterations`, `tools_allow`, `tools_deny`, `mcp_servers`, OpenRouter per-agent routing keys),
  - Markdown body becomes the specialist system prompt.
- `AppContainer` always loads those files at boot and builds `AgentRegistry`; disabled agents are filtered by frontmatter `enabled: false`.
- `Dispatcher` computes main-agent tool exposure via `tool_ownership_mode`:
  - `shared`: main agent keeps tools after main-agent allow/deny policy,
  - `exclusive`: tools available to specialists are hidden from main agent.
  - `exclusive_mcp`: only specialist-owned MCP tools are hidden; local/system tools stay shared.
- Delegation is executed by `invoke_agent` tool:
  1. validate requested specialist name,
  2. instantiate specialist client (provider/model overrides supported),
  3. filter tools by specialist policy,
  4. run specialist runtime with ephemeral in-turn state (no delegated SQLite transcript),
  5. return tool result to main agent for final answer synthesis.
- Delegated tool-use enforcement is configurable via `[orchestration].delegated_tool_call_policy`:
  - `auto` (default): requires at least one specialist tool call when delegated agent has any scoped tools,
  - `always`: requires at least one specialist tool call for every delegation,
  - `never`: disables delegated tool-call enforcement.
- Metadata emitted includes execution trace (`primary_agent`, `delegation_fallback_used`, `agent_trace`).
- Recursive delegation is blocked for specialists by removing `invoke_agent` from specialist tool scope.

Current notes:

- Agent subsystem is always available; there is no global enable/disable switch.

## Infrastructure Adapters

- Config:
  - `adapters/config/schema.py` holds Pydantic settings models.
  - `adapters/config/loader.py` resolves TOML + environment placeholders.
- Container:
  - `adapters/container/app_container.py` wires singleton-style service graph.
- Shared SQLAlchemy utilities:
  - `adapters/sqlalchemy_utils.py` provides `resolve_sqlite_storage_path` and `ensure_parent_dir` used by memory and scheduler adapters.
- Logging:
  - `adapters/logging/setup.py` configures structured logfmt-friendly logging.
- Messaging:
  - `adapters/messaging/console/service.py` handles local console I/O with EventBus publish/subscribe semantics.
  - `adapters/messaging/telegram/service.py` handles Telegram authorization, inbound text/media extraction, outbound message sending, and long-message chunking.
  - `adapters/messaging/telegram/incoming_media_mapper.py` normalizes media-target paths and `IncomingFileRef` mapping for photo/document/audio/voice uploads.
- Files:
  - `adapters/files/local_storage.py` handles managed workspace path-safe list/write/read operations.
- MCP:
  - `adapters/mcp/client.py` provides MCP JSON-RPC clients over stdio and HTTP (including HTTP session header reuse).
- Memory:
  - `adapters/memory/sqlalchemy.py` persists chat history.
  - `adapters/memory/kv_sqlalchemy.py` persists KV tool memory.
- Scheduler persistence:
  - `adapters/scheduler/sqlalchemy_prompt_store.py` stores scheduled prompts in SQLite via SQLAlchemy.

## LLM Layer

- `llm/provider_factory.py`: high-level `LLMClient` that orchestrates generate/step flows and delegates request assembly, schema fallback, tool execution, and usage parsing to `llm/services/*`.
- `llm/services/client_bootstrap.py`: provider construction, timeout/retry wiring, provider selection, and system-prompt loading (including HTTP/2 disablement for `http://` base URLs).
- `llm/services/request_builder.py`: canonical request kwargs/message assembly for `generate` and runtime `complete_once` calls.
- `llm/services/schema_policy.py` + `schema_fallback.py`: strict schema preparation and compatibility fallback behavior when providers reject schema mode.
- `llm/services/tool_executor.py` + `tool_loop_guard.py`: tool call execution and repeated-loop safeguards/fallback payloads.
- `llm/services/usage_parser.py` + `models.py`: usage/response parsing and typed return models (`LLMGeneration`, `LLMCompletionStep`, `LLMCompaction`).
- `llm/tools/factory.py`: builds enabled tool bindings from settings.
- `llm/tools/description_loader.py`: loads per-tool description strings from the `descriptions/` package at runtime.
- `llm/tools/*`: concrete tool schemas + handlers:
  - agent delegation (`list_agents`, `invoke_agent`),
  - chat memory management (`chat_history_info`, `chat_history_trim`),
  - calculator (`calculator`, `calculate_expression`),
  - HTTP client (`http_client`, `http_request`),
  - user/KV memory (`memory` action facade),
  - Python execution (`python_execute`, `python_environment_info`),
  - shell execution (`bash`),
  - structured patch editing (`apply_patch`),
  - file storage/workspace tools: `filesystem` action facade (list/glob/info/write/move/delete/send), `glob_files`, `read_file`, `grep`, `self_insert_artifact` (path confinement defaults to `tools.file_storage.root_dir` and can be relaxed with `allow_outside_root`),
  - audio transcription: `transcribe_audio` (backed by `audio_transcription_facade.py` for model lifecycle/transcription normalization),
  - scheduler controls (`schedule` action facade, `schedule_prompt`, `list_scheduled_prompts`, `cancel_scheduled_prompt`, `delete_scheduled_prompt`),
  - time helpers (`current_datetime`, `datetime_now`).

## MCP Tool Bridge Flow

When MCP is enabled, tool registration and invocation follow this flow:

1. `llm/tools/factory.py` checks `settings.tools.mcp.enabled` and iterates configured `tools.mcp.servers`.
2. For each server, `MCPClient.list_tools_blocking()` performs discovery via JSON-RPC `tools/list` using either stdio or HTTP transport.
3. `MCPToolBridge.build_bindings()` filters discovered tools (`enabled_tools`/`disabled_tools`) and creates local LLM tool bindings.
4. Generated tool names follow `<name_prefix>_<server_name>__<remote_tool_name>` to keep each server namespace explicit.
5. On tool invocation, bridge handlers map local names back to remote tool names and call `MCPClient.call_tool_blocking(...)`.
6. Tool results are normalized before returning to runtime (content arrays are flattened into text when needed).

Transport details:

- Stdio transport keeps a subprocess per server, serializes I/O with asyncio locks, and performs initialize/initialized handshake.
- HTTP transport sends JSON-RPC requests to `url` and reuses `mcp-session-id` when provided by the server response headers.

Security boundary:

- MCP tools expose external capabilities. Use per-server `enabled_tools`/`disabled_tools` to enforce least privilege.

## Scheduler Model (Current)

The scheduler currently focuses on scheduled prompts (not a generic task DAG engine).

- Jobs are persisted in SQLite (`scheduled_prompts` table).
- Service leases due jobs, dispatches them through the event bus, retries failures with backoff, and supports interval recurrence.
- Scope checks enforce owner/channel/chat/user constraints for cancel/delete/list operations.
- Deletion is explicit user-triggered behavior; active jobs are cancelled before hard delete.
- There is no first-class scheduler notification suppression flag yet (for example `notify_user=false`); outbound reply suppression still depends on normal handler response metadata.

## Multimodal Input Path (Telegram -> LLM)

- Telegram adapter can ingest `photo`, `document`, `audio`, and `voice` updates when media is enabled in config.
- Incoming uploads are persisted as managed files and exposed through `metadata.incoming_files`.
- `audio`/`voice` include `duration_seconds` in `IncomingFileRef` metadata when Telegram provides it.
- `LLMMessageHandler` delegates short-audio auto-transcription to `AudioAutoTranscriptionService` (through `ToolBindingAudioTranscriptionExecutor`) before normal LLM generation, when enabled.
- Attachments provided as direct multimodal payloads are normalized into provider-specific formats:
  - responses-style `input_image`/`input_file` for Responses providers,
  - chat-completions-compatible `image_url`/`file` for chat-completions providers.
- For non-supporting provider modes, handler returns a clear user-facing message and avoids invalid provider calls.

## Data and State

- Conversation history: SQLite transcript store (optional max-history trimming and token-threshold compaction).
- KV notes: optional SQLAlchemy-backed store under tool controls.
- Scheduled prompts: SQLite prompt store with recurrence + retry metadata.
- Runtime queue state: in-process, ephemeral, reconstructed on restart from durable stores.

## Configuration Surface

`config.example.toml` is the canonical reference (with inline notes for production-oriented values).
`config.yolo.toml` is a Docker-oriented full-capability profile (pre-enabled tools + Playwright MCP server).

Main sections:

- `[runtime]`
- `[channels.telegram]` (auth allowlists, mode, media limits)
- `[llm]`
  - `llm.prompts_dir` points to channel prompt packs (default `./prompts`)
- `[orchestration]` (definitions directory, delegated runtime timeout defaults, main-agent policy, `main_tool_use_guardrail`: `"disabled"` | `"llm_classifier"`)
- `[orchestration.main_agent]` (main-agent tool allow/deny)
- `[memory]`
- `[scheduler.prompts]`
- `[tools.*]` (`kv_memory`, `http_client`, `calculator`, `python_exec`, `bash`, `apply_patch`, `time`, `file_storage`, `grep`, `audio_transcription`, `browser`, `mcp`)
- `[logging]`

Agent definition files under `./agents` are part of the effective config surface.

Container profile notes:

- `docker-compose.yml` mounts `config.toml` as `/app/config.toml` by default.
- `config.yolo.toml` is an optional reference profile for users who want pre-enabled tools/MCP.
- Docker image includes all Poetry extras plus additional Python packages from `docker-requirements.txt`.

## Testing Strategy

- Unit and integration-style tests under `tests/` mirror runtime modules.
- Coverage focuses on config loading, event bus behavior, handler/tool flows, scheduler persistence/service behavior, provider interface, and Telegram adapter authorization/media mapping.
- Async paths are validated with `pytest` + `pytest-asyncio`.

## Current Boundaries and Future Extensions

Current architecture supports:

- daemon Telegram channel and interactive console channel,
- one daemon process with in-process event bus,
- SQLite-backed persistence for memory and scheduled prompts,
- tool-augmented LLM interactions,
- main-agent-driven tool-based delegation (`invoke_agent`) with per-agent tool scoping.

Natural extension points already in place:

- add new messaging adapters under `adapters/messaging/`,
- add alternative persistence adapters behind existing protocols,
- add richer control-plane interfaces (HTTP/WebSocket) without rewriting core dispatch flow,
- evolve scheduled prompts into broader task orchestration while preserving event bus contracts,
- extend single-hop delegation into deeper orchestration if/when recursive delegation is introduced.
