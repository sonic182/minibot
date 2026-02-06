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
- [ ] Introduce SQLAlchemy-backed key/value store with tool-call surface so the LLM can save, retrieve, and search structured notes gated by `owner_id`.
- [ ] Add configuration toggles for the KV store and ensure handlers wire tools only when enabled.
- [ ] Document tool usage patterns and cover repository/tool tests.

## Stage 2 – Scheduler & File-Based Task Store
- [ ] Implement domain/job models, scheduler facade, and asyncio scheduler adapter persisting jobs via the file-backed task storage (`flock` guarded).
- [ ] Introduce task service and strategy abstraction for delayed/recurrent tasks, plus CRUD commands exposed through the bot.
- [ ] Extend event bus consumers/producers for job completion + task lifecycle events.
- [ ] Expand tests to cover scheduler timing, persistence crash recovery, and task CRUD flows.

## Stage 3 – Advanced Persistence & Additional Channels
- [ ] Add SQLite task store adapter (via SQLAlchemy) and optional Redis/queue integrations; support migrations/alembic.
- [ ] Implement additional messaging ports (e.g., Slack) reusing the channel abstraction and event bus.
- [ ] Provide HTTP/WebSocket control plane + optional dashboard for monitoring tasks, jobs, and channel metrics.
- [ ] Broaden test matrix with integration tests (e.g., using testcontainers) and contract tests per channel adapter.

## Stage 4 – Tooling Ecosystem & Observability
- [ ] Expand task strategies to include external tools (web fetch, code exec, workflow orchestration) with permissioning.
- [ ] Add metrics/tracing pipeline (OpenTelemetry/Prometheus) and alerting hooks.
- [ ] Release automation + deployment manifests (Docker/systemd/k8s) and soak tests for multi-instance topologies.
