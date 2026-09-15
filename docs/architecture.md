---
myst:
  html_meta:
    description: "Minibot architecture overview — hexagonal layout, runtime flow, subsystems, and where each module lives."
    keywords: "minibot architecture, hexagonal architecture, Python AI agent internals"
---

# Architecture

MiniBot is an asyncio application with two runtime entrypoints: **daemon mode**
(`minibot.app.daemon`) for Telegram, and **interactive CLI mode**
(`minibot.app.console`) for local console conversations. Both publish inbound events
to an internal event bus, route messages through the LLM pipeline, and emit outbound
responses back to the active channel adapter. A third, deliberately narrower
entrypoint is the forked task worker (`minibot.adapters.tasks.worker`).

## Guiding principles

- **Lightweight hexagonal split** — `core` (domain contracts), `app` (orchestration),
  `adapters` (infrastructure), `llm` (provider/tool integration).
- **Async-first** boundaries for I/O-heavy paths (Telegram, DB, provider calls).
- **Replaceable infrastructure** behind protocols (memory repositories, scheduled
  prompt store, tools, vector stores).
- **Explicit, testable flow** with dependency wiring centralized in the container.

## Repository layout

Directories only; module-level detail lives in the linked pages below. Dependencies
point inward — `adapters` and `llm` may import `core`; `core` imports none of them.

```text
minibot/
  app/        orchestration: daemon, dispatcher, handler, agent runtime, extensions API
    handlers/services/   LLM turn collaborators
  core/       domain models + protocols
  adapters/   infrastructure: config, container, logging, http, memory, graph,
              vectors, qdrant, messaging, scheduler, tasks, files, mcp, vault
  llm/        provider factory + tool schemas/handlers (providers/, services/, tools/)
  extensions/ bundled extensions: thin register(mb) composition
  rag/        ingestion, chunking, embeddings, retrieval
  shared/     small cross-cutting helpers
```

## Runtime flow

1. Entry point (`minibot.app.daemon` or `minibot.app.console`) boots settings,
   logging, memory, tools, and dispatcher. The daemon first replays turns marked
   pending by a previous crash, then optionally starts the built-in HTTP server.
2. A channel adapter maps input into `ChannelMessage` and publishes `MessageEvent`.
3. `app.event_bus.EventBus` delivers the event to `app.dispatcher.Dispatcher`.
4. The dispatcher builds main-agent tool visibility (allow/deny policy, exclusive
   ownership mode) and invokes `LLMMessageHandler`.
5. The handler loads history, composes system prompt fragments, and builds tool context.
6. The main agent (`minibot`) runs its tool loop in `AgentRuntime`.
7. Delegation is tool-driven: `invoke_agent` resolves a specialist, applies its tool
   policy, and runs an ephemeral in-turn runtime; the result returns to the main agent.
8. The handler returns `ChannelResponse` with metadata (`primary_agent`, `agent_trace`,
   `delegation_fallback_used`, token trace).
9. The dispatcher publishes `OutboundEvent`; the active channel renders it to the user.

## Console agent invocation

```mermaid
flowchart TD
    U[User in terminal] --> C[minibot console]
    C --> CS[ConsoleService.publish_user_message]
    CS --> EB[(EventBus)]
    EB --> D[Dispatcher]
    D --> H[LLMMessageHandler.handle]
    H --> SUP[Main Agent Runtime]
    SUP --> DEC{Need specialist?}
    DEC -->|no| FINAL[Main agent final answer]
    DEC -->|yes| IA[invoke_agent tool]
    IA --> AR[AgentRegistry lookup]
    AR --> S[Specialist AgentRuntime]
    S --> T[Specialist tool calls + output]
    T --> SUP
    FINAL --> RESP[ChannelResponse + metadata]
    RESP --> EB2[(OutboundEvent)]
    EB2 --> C
    C --> U2[Rendered response in terminal]
```

## Layer map

- `core` — domain contracts: `AgentSpec`, runtime state, channel DTOs, events,
  memory/graph/task/vector protocols.
- `app` — event bus, dispatcher, `LLMMessageHandler` and its services, agent
  runtime/registry/policies, extension API, guardrails, scheduler and task services.
- `adapters` — config loading/validation, container, logging, the optional HTTP
  server, SQLite/SQLAlchemy memory, Telegram/console/RabbitMQ messaging, scheduler
  persistence, subprocess task workers, local files, MCP clients, credential vault.
- `llm` — provider factory and services (request building, schema policy, tool
  execution, usage parsing, compaction), plus the LLM-facing tool schemas/handlers.
- `extensions` — bundled, opt-in `register(mb)` modules (tools, channels,
  integrations, services).
- `rag` — document ingestion, chunking, embeddings, reranking, retrieval.
- `shared` — generic helpers (prompt loading, frontmatter, paths, retries).

## Data and state

- Conversation history: SQLite transcript store (optional trimming/compaction).
- Pending turns: SQLite rows marking in-flight turns, replayed by the daemon after a crash.
- KV notes: optional SQLAlchemy-backed store under tool controls.
- Scheduled prompts: SQLite prompt store with recurrence + retry metadata.
- Vector index: whichever `VectorStore` backend `[tools.rag]` selects.
- Credentials: encrypted `secrets.vault.yml`, decrypted into process memory only.

## Where to go next

- Extending MiniBot: {doc}`extending`, {doc}`extensions`, {doc}`extension-api`, {doc}`events`
- Agents and prompts: {doc}`agents`, {doc}`prompts`
- Tools and integrations: {doc}`tools`, {doc}`mcp`, {doc}`tasks`, {doc}`scheduler`, {doc}`graph`
- Providers: {doc}`providers`
- Retrieval and media: {doc}`rag`, {doc}`audio`
- Operations: {doc}`config`, {doc}`cli`, {doc}`security`, {doc}`vault`
