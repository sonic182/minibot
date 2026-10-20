---
name: minibot-dev
description: Orientation and change-routing for developing MiniBot itself. Use before adding or changing a tool, channel, specialist agent, prompt fragment, config section, event handler, task worker or runtime skill. Not for reviewing an existing diff (python-hex-review) or writing tests (minibot-testing).
---

# MiniBot Dev

Route a change to the right files and surface the invariants that are not visible in the file
tree. This skill points into the code; it does not restate the architecture.

References in this skill and its `references/` name a file and a symbol, never a line number:
locate the definition with `find_symbol` (gmem) or `rg`. Keep it that way when editing them —
line numbers go stale with every change.

## Orientation

```
minibot/
  core/        domain contracts and ports only — no framework, transport, DB, provider or filesystem
  config/      Settings schema (`schema.py`), `${ENV}` expansion, config-path resolution
  app/         orchestration + policy (dispatcher, handlers, agent runtime, extensions API, tasks)
  adapters/    concrete integrations (config loader/configurator, container, messaging, memory, HTTP UI, MCP, …)
  llm/         provider wiring, request shaping, schema policy, tool definitions
  rag/         chunking/embedding/retrieval over the `VectorStore` port
  shared/      small generic helpers (prompt_loader, frontmatter, path_utils)
  extensions/  thin bundled `register(mb)` composition modules
  skills/      native runtime skills bundled with the package
```

Entrypoints: `app/daemon.py` (Telegram + web UI) and `app/console.py` (CLI) boot the same spine;
the forked task worker (`app/tasks/worker.py`) is a third, deliberately narrower one. Layer and
import rules are owned by the `python-hex-review` skill. Architecture overview:
`docs/architecture.md`. Build/lint/test commands and project rules: `AGENTS.md`.

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
| A web UI page or layout | — | `responsive-grids` skill |
| Tests for any of the above | — | `minibot-testing` skill |
| An end-to-end check that the agent loop or a task worker really runs | — | `minibot-smoke` skill |
| A review of a change already made | — | `python-hex-review` skill |

Read [CONVENTIONS.md](references/CONVENTIONS.md) before your first edit in this repo: what CI
actually gates, and the traps (tool scoping, alias collisions, MCP prefix, implicit bundles).

## Workflow

1. **Locate before you create.** Most additions already have a sibling to copy. Find it and
   match its shape rather than inventing a new one.
2. **Read the whole target file before editing it.** Wiring here is order-sensitive.
3. **Prefer the extension path** (see *Lean core*). A `register(mb)` module needs no schema,
   factory or description-file wiring.
4. **Check every entrypoint your change touches.** Daemon, console and worker assemble tools
   and extensions *differently* — a tool wired only into `app/tool_factory.py` is invisible to
   workers (EXTENSION_POINTS.md §6).
5. **Update the docs surface.** A new or changed config option goes into its config model's
   docstring (rendered by `docs/config.rst`), `config.example.toml` and the relevant `docs/*.rst`;
   a file added to or removed from `prompts/` goes into `docs/prompts.rst`. The docs build runs
   `sphinx-build -W`, so a new warning fails it.
6. **Lint** with the `AGENTS.md` commands. Ruff is not enforced in CI; the pylint name rule is
   (see *Hard rules*).

## Lean core

If it works as an extension, it stays out of the core. Full rule: `docs/architecture.md`
(*Lean core*).

- **Extension first.** Anything buildable from `ExtensionContext` (`app/extensions.py`) is a
  bundled `register(mb)` module, not a branch in `build_enabled_tools` (`app/tool_factory.py`).
- **Core keeps only** tools that need state `ExtensionContext` does not expose (conversation
  memory backend, skill registry, managed storage, config path) and the wrappers every tool
  passes through (approval, output spill, tool-call events).
- **Generalize, don't special-case.** If a feature needs something `ExtensionContext` lacks,
  propose widening the extension API before adding a core-only branch.
- **Extensions are first-class.** A core mechanism acting on tools (visibility policy,
  approval, output spill, worker allowlist) must handle extension tools exactly like core ones.
  Reject a design that only works for core tools until it is generalized.

## Hard rules

`AGENTS.md` (git, lint, comments, style, output classification) applies in full. Beyond it:

- **No 1–2 character identifiers** except `i j x y mb on ok`, in `minibot/`. This is the only
  style rule CI enforces (`[tool.pylint.basic]` in `pyproject.toml`), which is why the codebase
  writes `except … as exc`.
- **Never classify LLM intent, state or provider errors by text matching.** Live reference:
  `LLMClassifierToolUseGuardrail` (`app/tool_use_guardrail.py`) + `ToolGuardrailPayload`
  (`app/tool_guardrail_validator.py`); for provider errors, `ProviderHTTPError` (`llm/errors.py`).
- **Preserve `${ENV}` and `${secret:NAME}` references when writing configuration.** Contract in
  `docs/config.rst`; traps in CONVENTIONS.md.
