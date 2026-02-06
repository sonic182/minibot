MiniBot
=======

Asynchronous Telegram assistant that routes user prompts through `sonic182/llm-async` providers.

Stage 1 targets:

1. Telegram-only channel with inbound/outbound DTO validation via `pydantic`.
2. SQLite/SQLAlchemy-backed conversation memory for context/history.
3. Structured `logfmter` logs with request correlation and event bus-based dispatcher.
4. Pytest + pytest-asyncio tests for config, event bus, memory, and handler plumbing.

Mini Hex Architecture
---------------------

MiniBot follows a lightweight hexagonal layout described in detail in `ARCHITECTURE.md`. The repository root keeps
`minibot/` split into:

- `core/` – Domain entities and protocols (channel DTOs, memory contracts, future job models).
- `app/` – Application services such as the daemon, dispatcher, handlers, and event bus that orchestrate domain + adapters.
- `adapters/` – Infrastructure edges (config, messaging, logging, memory, scheduler, LLM providers) wired through the
  DI container.
- `llm/` – Thin wrappers around `llm-async` providers plus `llm/tools/`, which defines the tool schemas and handlers that expose bot capabilities (KV memory, future scheduler hooks) to the model.
- `shared/` – Cross-cutting utilities.

Tests under `tests/` mirror this structure so every layer has a corresponding suite. This “mini hex” keeps the domain
pure while letting adapters evolve independently.

Quickstart
----------

1. `poetry install`
2. `cp config.example.toml config.toml`
3. Populate secrets in `config.toml` (bot token, allowed chat IDs, provider key).
4. `poetry run python -m minibot.app.daemon`

Up & Running with Telegram
---------------------------

1. Launch Telegram [`@BotFather`](https://t.me/BotFather) and create a bot to obtain a token.
2. Update `config.toml`:
   * set `channels.telegram.bot_token`
   * populate `allowed_chat_ids` or `allowed_user_ids` with your ID numbers
   * configure the LLM provider section (`provider`, `api_key`, `model`)
3. Run `poetry run python -m minibot.app.daemon` and send a message to your bot. Expect a simple synchronous reply (LLM, memory backed).
4. Monitor `logs` (Logfmt via `logfmter`) and `htmlcov/index.html` for coverage during dev.
