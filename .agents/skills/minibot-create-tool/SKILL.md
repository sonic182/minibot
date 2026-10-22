---
name: minibot-create-tool
description: Guided wizard for adding a new LLM tool to MiniBot. Asks targeted questions, picks a standalone extension (default) or a bundled core tool, then generates the files. Use when the user wants a new tool. Not for channels, agents, prompts, or rewiring existing tools (minibot-dev).
---

# MiniBot Create Tool

Collect the tool's requirements, pick the integration path, then generate the files for that
path. The `minibot-dev` skill owns the wiring chain and project invariants; this skill owns the
templates. File templates live in [references/TOOL_PATTERNS.md](references/TOOL_PATTERNS.md) —
read the section for the chosen path before Step 3.

## Step 0 — Extension or core?

Decide *before* collecting requirements and tell the user which path you are using:

- **Extension (default)** — a standalone `register(mb)` module enabled via
  `[extensions].modules`. No factory, config-schema, or description-file wiring; the docstring
  is the description and the first argument's pydantic model is the schema. Best for
  user-specific, one-off, or experimental tools.
- **Core (bundled)** — the tool ships inside MiniBot: a tool class under `minibot/llm/tools/`,
  optional `[tools.<key>]` config in `config/schema.py`, and registration from a bundled
  extension module under `minibot/extensions/tools/`. Choose this only when the tool belongs in
  the shipped surface. Shipping with MiniBot does not make a tool core-wired:
  `build_enabled_tools` is only for tools that need state `ExtensionContext` does not expose
  (*Lean core* in the `minibot-dev` skill).

Recommend the extension path unless the user says otherwise.

## Step 1 — Gather requirements

Ask the user these questions **in one message** (do not ask them one at a time):

1. **Tool name** — the snake_case name the LLM will call (e.g. `send_email`).
2. **Purpose** — one sentence: what does this tool do?
3. **Parameters** — for each: name, type (`string`, `integer`, `boolean`, `number`), required or optional, short description.
4. **Context needed?** — which `ToolContext` fields (`core/tools.py`) the handler needs, e.g. `owner_id`, `channel`, `chat_id`, `user_id`.
5. **Multiple tools?** — does this module expose more than one tool? If yes, their names and parameters.
6. **Config settings** — core tool: beyond `enabled: bool`, which runtime settings (timeout, max size, API key)? Extension: which settings live in `[extensions.config.<module>]`?
7. **Dependencies** — any injected dependency (a storage backend, the `EventBus`, a third-party client)? Name it and its type.

Wait for the answers before proceeding. If the tool name is one of the aliased names
(`http_client`, `calculator`, `datetime_now`, `artifact_insert`) or already exists, ask for
another: names must be unique after alias canonicalization (`minibot-dev` CONVENTIONS.md).

## Step 2 — Confirm plan

Summarize what you will generate **for the chosen path**, then ask for confirmation.

Extension path:
- `<module>.py` — `register(mb)` plus the tool(s) via `@mb.tool` (or `mb.add_tool`)
- `config.toml` snippet — `[extensions].modules = ["<module>"]` and any `[extensions.config.<module>]` slice

Core path:
- `minibot/llm/tools/<module>.py` — tool class
- `minibot/llm/tools/<tool_name>.txt` — LLM-facing description, one per tool name
- config model + `ToolsConfig` field in `config/schema.py` (only if settings are needed)
- registration: the bundled module under `minibot/extensions/tools/<domain>.py` that gets the
  entry, or — only for the lean-core exception — the branch in `app/tool_factory.py::build_enabled_tools`
- `config.example.toml` snippet and docs (`docs/tools.rst`; `docs/config.rst` renders the config model docstring)
- worker visibility, if the user wants task workers to have the tool (Step 3d)

## Step 3 — Generate files

Read each target file before editing it.

### 3a. Extension path

Write a single `<module>.py` from TOOL_PATTERNS.md → *Extension tool*.

- Module-level `def register(mb: ExtensionContext) -> None`.
- `@mb.tool` on `async def <tool_name>(args: <Model>, context: ToolContext)`: function name =
  tool name, docstring = description, first argument's pydantic model = JSON schema.
- Define pydantic argument models **at module scope**, not inside `register()` — `mb.tool`
  raises at registration otherwise, and so does a missing docstring. Either aborts startup.
- Use `mb.add_tool(binding)` instead when you need a custom name, a hand-written schema, or a
  description loaded from a file.
- Return `dict[str, Any]`; raise `ToolInputError` (`shared/errors.py`) for invalid arguments.
- Settings come from `mb.config` (the `[extensions.config.<module>]` slice); `mb.settings` is
  the full validated config.
- The module must be importable by name (on `PYTHONPATH` or installed); `load_extensions`
  imports it with `importlib.import_module` and fails startup if it cannot.

### 3b. Core path — tool class and description

`minibot/llm/tools/<module>.py` from TOOL_PATTERNS.md → *Core tool class*.

- Class name: `PascalCase` matching the module name.
- `bindings(self) -> list[ToolBinding]` returns one `ToolBinding` per tool.
- `_schema(self) -> Tool` (or `_<name>_schema`) uses `load_tool_description("<tool_name>")`.
- `async _handle(self, payload, context) -> dict[str, Any]` — always async; name the context
  parameter `_` when unused.
- Build the schema with `schema_utils.py` helpers and coerce payload fields with `arg_utils.py`
  helpers (tables in TOOL_PATTERNS.md).
- Return `dict[str, Any]`; return a `ToolResult` (`core/agent_runtime.py`) only for directives
  (e.g. `self_insert_artifact` in `llm/tools/file_storage.py`).

Write `minibot/llm/tools/<tool_name>.txt` per TOOL_PATTERNS.md → *Description file*. An empty or
missing file aborts startup.

### 3c. Core path — config and registration

Only when the tool needs settings, in `config/schema.py` (read it first):

1. `class <Name>ToolConfig(BaseModel)` with `enabled: bool = False` and any extra fields, near
   related tool configs, with a docstring naming its `[tools.<key>]` section and fields.
2. A field `<key>: <Name>ToolConfig = <Name>ToolConfig()` on `ToolsConfig`.

Register it from the matching bundled module under `minibot/extensions/tools/` (TOOL_PATTERNS.md
→ *Registration*). Wire it into `build_enabled_tools` instead only when it needs state
`ExtensionContext` does not expose (conversation memory backend, skill registry, managed
storage, config path). Being always-on is not a reason: an extension can register
unconditionally. If the missing state is generic, propose widening `ExtensionContext` instead.

Add the `[tools.<key>]` block to `config.example.toml` and the tool to `docs/tools.rst`.

### 3d. Worker visibility (either path, only if asked)

Task workers do not see most tools by default. Follow `minibot-dev` EXTENSION_POINTS.md §6:
a bundled extension module needs the worker branch of `_bundled_modules`; a core tool needs
`_build_worker_tools` **and** `_WORKER_TOOL_ALLOWLIST` in `app/tasks/worker.py`.

## Step 4 — Lint

Run `poetry run ruff check --fix` and `poetry run ruff format` on the files you touched and
report any issues. Check new identifiers against the CI name rule (no 1–2 character names
except `i j x y mb on ok`).

## Rules

- Never skip a file for the chosen path; extension-path work must not touch `config/schema.py`
  or `app/tool_factory.py`.
- Do not write tests unless the user asks; when they do, use the `minibot-testing` skill.
- `AGENTS.md` rules (no text-matching for semantic decisions, comments, style) apply to the
  generated code.
