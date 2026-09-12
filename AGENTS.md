# Agent Guidelines for MiniBot

## ⚠️ IMPORTANT: Git Operations
**NEVER execute git "write" commands**: Do NOT use `git commit`, `git push`, `git add`, or any git write commands (unless user explicitly ask for it). Normally just use edit files directly via the Edit/Write tools; the user handles commits.

## Build / Run / Lint
- **Install dependencies**: `poetry install --all-extras` (always include extras so test dependencies are available)
- **Run daemon**: `poetry run python -m minibot.app.daemon` (or `python main.py` for quick checks)
- **Lint**: `poetry run ruff check --fix minibot tests` (or `poetry run ruff check .` to scan everything); `flake8` is not configured here.
- **Format**: `poetry run ruff format .`
- **Tests**: `poetry run pytest` (single test via `poetry run pytest <file>::<TestClass>::<test_method>` or `poetry run pytest <file> -k <test_name>`)

## Documentation & Comments
- **Comments/docstrings** — avoid incidental comments; public documentation docstrings are acceptable when they feed generated docs or clarify public config/tool surfaces.
- **No automated tests** unless asked — prefer linting or formatting to verify changes.
- **Linting is welcome** — run `ruff check` or `ruff format`; this repo does not configure `flake8` despite it being available.

## Output Classification Rule
- **NEVER classify LLM intent/state by regex, substring, or ad-hoc text matching.**
- Use structured schema fields (typed status/error fields) or model-native/tool-native structured outputs. Reference implementation: the tool-use guardrail returns an `extra="forbid"` pydantic payload (`app/tool_guardrail_validator.py`, `app/tool_use_guardrail.py`).
- Text matching is acceptable only for deterministic protocol/format parsing (for example markdown fences, SSE framing), not for semantic decision-making.

## Code Style
- **Line length**: 119 characters.
- **Python version**: 3.12–3.14 (`pyproject.toml` constraint: `>=3.12,<3.15`) with explicit type hints (Pydantic models, protocols, etc.).
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
- **Memory backend**: SQLite/SQLAlchemy via `aiosqlite` powers Stage 1 conversation history.
- **Config**: use `config.toml` to configure runtime, channels, logging, scheduler, memory, tools (`kv_memory`, `http_client`, `file_storage`, `audio_transcription`, `mcp`), and tasks. There is no `${ENV_VAR}` interpolation — the loader is plain `tomllib`, and the only environment variable it reads is `MINIBOT_CONFIG`. To pull a secret from the environment, store the variable *name* in config (`token_env = "GITHUB_TOKEN"`) and call `os.environ.get(...)` in the consuming code.
