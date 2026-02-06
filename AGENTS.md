# Agent Guidelines for MiniBot

## ⚠️ IMPORTANT: Git Operations
**NEVER execute git "write" commands**: Do NOT use `git commit`, `git push`, `git add`, or any git write commands. Only edit files directly via the Edit/Write tools; the user handles commits.

## Build / Run / Lint
- **Install dependencies**: `poetry install`
- **Run daemon**: `poetry run python -m minibot.app.daemon` (or `python main.py` for quick checks)
- **Lint**: `poetry run ruff check --fix minibot tests` (or `poetry run ruff check .` to scan everything); `flake8` is not configured here.
- **Format**: `poetry run ruff format .`
- **Tests**: `poetry run pytest` (single test via `poetry run pytest <file>::<TestClass>::<test_method>` or `poetry run pytest <file> -k <test_name>`)

## Documentation & Comments
- **No extra comments** — avoid adding comments or docstrings unless explicitly requested.
- **No automated tests** unless asked — prefer linting or formatting to verify changes.
- **Linting is welcome** — run `ruff check` or `ruff format`; this repo does not configure `flake8` despite it being available.

## Code Style
- **Line length**: 119 characters.
- **Python version**: 3.10+ with explicit type hints (Pydantic models, protocols, etc.).
- **Imports**: group as standard library → third-party → local.
- **Naming**: `snake_case` for functions/variables, `PascalCase` for classes.
- **Async-first**: favor `asyncio`-compatible async/await patterns for I/O-bound code.
- **Date handling**: use timezone-aware datetimes.
- **Error handling**: raise `ValueError` in validators, log/handle others deliberately.
- **No `print`** (Ruff T201) — rely on structured logging.

## Architecture & Roadmap
- **Mini hex layout**: `minibot/` contains `app` (daemon, dispatcher, handlers), `core` (domain models/protocols), `adapters` (config, messaging, memory, logging, scheduler, etc.), `llm/` (provider factory + `llm/tools/` for LLM tool schemas/handlers), and `shared` helpers. Tests mirror this split so each layer has a dedicated suite. See `ARCHITECTURE.md` for the full map.
- **Entry point**: `minibot.app.daemon` bootstraps config via `AppContainer`, wires dependencies, starts dispatcher + channel services, and uses graceful shutdown with signal handlers.
- **Event bus**: `app/event_bus.py` abstracts an `asyncio` queue with async iterators; keep observability (queue depth, latency) in mind for future monitoring.
- **Memory backend**: SQLite/SQLAlchemy via `aiosqlite` powers Stage 1 conversation history; future adapters include Redis, Mongo, etc.
- **Roadmap**: check `TODO.md` for Stage breakdown—Stage 1 is complete (Telegram + SQLite memory), Stage 2 adds scheduler/task store, Stage 3 brings advanced persistence and new channels, Stage 4 introduces tooling/observability.
- **Config**: use `config.toml` (with `${ENV_VAR}` placeholders) to configure runtime, channels, logging, scheduler, memory, tools (`kv_memory`, HTTP client), and tasks.
