---
name: minibot-dev
description: Orientation and change-routing for developing MiniBot itself. Use before adding or changing a tool, channel, specialist agent, prompt fragment, config section, event handler, or task worker, or when you need the wiring chain and project invariants for a change.
---

# MiniBot Dev

Start here before adding or changing anything in MiniBot. This skill routes a change to the
right files; it does not restate `ARCHITECTURE.md`, it points into it.

## Orientation

```
minibot/
  core/      domain contracts only — no framework, transport, DB, provider or filesystem
  app/       orchestration + policy (dispatcher, handlers, agent runtime, extensions API)
  adapters/  concrete integrations (config, messaging, memory, tasks, scheduler, logging, MCP)
  llm/       provider wiring, request shaping, schema policy, tool definitions
  rag/       chunking/embedding/retrieval
  shared/    small generic helpers (prompt_loader, frontmatter, path_utils)
  extensions/ thin bundled `register(mb)` composition modules
```

Dependencies point inward: `adapters` and `llm` may import `core`; `core` imports none of them.
Two entrypoints boot the same spine — `app/daemon.py` (Telegram) and `app/console.py` (CLI) —
plus a third, deliberately narrower one: the forked task worker (`adapters/tasks/worker.py`).

Long-form reference: `ARCHITECTURE.md`. Build/lint/test commands: `AGENTS.md`.

## Routing table

| I want to add… | Read | Owned by |
|---|---|---|
| An LLM tool | [EXTENSION_POINTS.md](references/EXTENSION_POINTS.md) §1 | `minibot-create-tool` skill |
| A messaging channel | [EXTENSION_POINTS.md](references/EXTENSION_POINTS.md) §2 | — |
| A specialist agent | [EXTENSION_POINTS.md](references/EXTENSION_POINTS.md) §3 | — |
| A config section | [EXTENSION_POINTS.md](references/EXTENSION_POINTS.md) §4 | — |
| A system-prompt rule | [PROMPT_COMPOSITION.md](references/PROMPT_COMPOSITION.md) | — |
| An event handler / dispatcher hook | [EXTENSION_POINTS.md](references/EXTENSION_POINTS.md) §5 | — |
| Task-worker behaviour | [EXTENSION_POINTS.md](references/EXTENSION_POINTS.md) §6 | — |
| A skill (for the bot's own runtime) | [EXTENSION_POINTS.md](references/EXTENSION_POINTS.md) §7 | — |
| Tests for any of the above | — | `minibot-testing` skill |
| A review of a change already made | — | `python-hex-review` skill |

Project conventions, real CI gates and the traps that are not visible in the file tree:
[CONVENTIONS.md](references/CONVENTIONS.md). Read it before your first edit in this repo.

## Workflow

1. **Locate before you create.** Most additions already have a sibling to copy. Find it and
   match its shape rather than inventing a new one.
2. **Read the whole target file before editing it.** Wiring here is order-sensitive.
3. **Prefer the extension path.** A `register(mb)` module needs no schema, factory or
   description-file wiring. Reach into `minibot/` core only when the thing belongs in the
   shipped surface.
4. **Check every entrypoint your change touches.** Daemon, console and worker assemble tools
   and extensions *differently* — a tool wired only into `factory.py` is invisible to workers.
5. **Lint**: `poetry run ruff check --fix minibot tests` and `poetry run ruff format .`.
   Note ruff is *not* enforced in CI; the pylint name rule is (see CONVENTIONS.md).
6. **No tests unless asked.** When asked, use the `minibot-testing` skill.
7. **Never run git write commands** — no `add`, `commit`, `push`. The user commits.

## Hard rules

- **Never classify LLM intent or state by regex, substring, or ad-hoc text matching.** Use
  structured schema fields or model-native structured output. Text matching is only for
  deterministic protocol parsing (markdown fences, SSE framing). Live reference implementation:
  `app/tool_use_guardrail.py:41` + `app/tool_guardrail_validator.py:11`.
- **No `print`** — Ruff `T201`; use structured logging.
- **No 1-2 character identifiers** except `i j x y mb on ok`. This is the only style rule CI
  enforces (`pyproject.toml:140`), which is why the codebase writes `except … as exc`.
- **Line length 119**, Python 3.12–3.14, explicit type hints, async-first on I/O paths,
  timezone-aware datetimes.
- **No comments** unless a constraint is genuinely non-obvious, or the docstring is a public
  documentation surface (tool descriptions, config models).
- **`config.toml` has no `${ENV}` interpolation.** Store the *variable name* in config and read
  `os.environ` in the consuming code.
