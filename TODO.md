# TODO Roadmap

Reference `ARCHITECTURE.md` for full design context; each stage below narrows the build focus and expected test coverage.

## Stage 1 – MVP (Telegram + SQLite Memory, No Scheduler)
- [x] Scaffold the hexagonal layout under `minibot/` (app/core/adapters/shared) with poetry scripts.
- [x] Implement config loader + container wiring to hydrate services from `config.toml` and env vars.
- [x] Set up structured logging via `python-logfmter` (logfmt default) and ensure correlation IDs propagate.
- [x] Provide in-process event bus abstraction and the main daemon loop supervising it alongside the Telegram channel service.
- [x] Implement Telegram adapter (aiogram/PTB async) with allowed user/chat whitelists and simple request→LLM response handler.
- [x] Integrate `sonic182/llm-async` provider factory with initial OpenAI-like provider; handle streaming + retries.
- [x] Ship memory backend using SQLite via `aiosqlite`/SQLAlchemy for storing conversation history/context.
- [x] Add pytest + pytest-asyncio test suite covering config, memory backend, event bus, and handler pipeline (with Telegram adapter mocked).

## Stage 1.5 – KV Memory Tools
- [x] Introduce SQLAlchemy-backed key/value store with tool-call surface so the LLM can save, retrieve, and search structured notes (owner ID resolved server-side).
- [x] Add configuration toggles for the KV store and ensure handlers wire tools only when enabled.
- [x] Document tool usage patterns and cover repository/tool tests.
- [x] Add optional HTTP client tool (aiosonic-based) with config toggles, logging, and tests.

## Stage 2 – Scheduled Prompts on SQLite
- [x] Implement domain/job models, scheduler facade, and asyncio scheduler adapter with SQLAlchemy-backed SQLite persistence for scheduled prompts.
- [x] Introduce scheduled prompt service for delayed/recurrent jobs, plus bot tools for schedule/list/cancel/delete flows.
- [x] Route scheduled prompt dispatch through the event bus as `MessageEvent` for normal handler processing.
- [ ] Add explicit scheduled-prompt notification policy (for example `notify_user=false`) so runs can update state/tools without outbound channel replies.
- [x] Add scheduler persistence coverage for lease timeout/re-acquire and retry/resume behavior.
- [ ] Expand tests to cover notification-suppression flows end to end.

## Already Implemented (Roadmap Catch-up)
- [x] Add interactive console runtime/channel (`minibot.app.console` + console messaging adapter) alongside daemon mode.
- [x] Add multi-agent delegation architecture (agent definitions, registry, per-agent tool policy, runtime delegation trace).
- [x] Add MCP tool bridge and expanded tool ecosystem (filesystem, python execution, browser-related MCP integration).

## Stage 3 – Advanced Persistence & Additional Channels
- [ ] Generalize scheduled-prompt persistence into broader task execution storage and optional Redis/queue integrations; support migrations/alembic.
- [ ] Implement additional messaging ports (e.g., Slack) reusing the channel abstraction and event bus.
- [ ] Provide HTTP/WebSocket control plane + optional dashboard for monitoring tasks, jobs, and channel metrics.
- [ ] Broaden test matrix with integration tests (e.g., using testcontainers) and contract tests per channel adapter.

## Stage 4 – Tooling Ecosystem & Observability
- [ ] Expand task strategies to include external tools (web fetch, code exec, workflow orchestration) with permissioning.
- [ ] Add metrics/tracing pipeline (OpenTelemetry/Prometheus) and alerting hooks.
- [ ] Release automation + deployment manifests (Docker/systemd/k8s) and soak tests for multi-instance topologies.
