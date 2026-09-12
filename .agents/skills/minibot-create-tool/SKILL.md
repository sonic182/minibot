---
name: minibot-create-tool
description: "Guided wizard for adding a new LLM tool to Minibot. Asks targeted questions, decides between a standalone Python extension (default) and a bundled core tool, then generates the files for that path. Use when the user wants to add a new tool to minibot."
---

# Minibot Create Tool

Guided wizard that collects tool requirements, picks the right integration path, then
generates the files for that path.

Start with the `minibot-dev` skill if you also need the architecture map, the wiring chain
behind either path, or the project invariants (notably: tool names must be unique *after*
alias canonicalization, and worker visibility is wired separately).

## Step 0 — Extension or core?

There are two ways to add a tool; decide *before* collecting requirements and tell the
user which one you are using:

- **Extension (default)** — a standalone ``register(mb)`` module enabled via
  ``[extensions].modules``. No factory, config-schema, or description-file wiring; the
  docstring is the description and the first argument's pydantic model is the schema.
  Best for user-specific, one-off, or experimental tools.
- **Core (bundled)** — the tool ships inside MiniBot itself: a tool class under
  ``minibot/llm/tools/``, optional ``[tools.<key>]`` config in ``schema.py``, and
  registration from a bundled extension module under ``minibot/extensions/tools/``
  (or wired into ``build_enabled_tools`` for always-on tools). Choose this only when
  the tool belongs in the shipped surface.

Recommend the extension path unless the user says otherwise.

## Step 1 — Gather requirements

Ask the user these questions **in one message** (do not ask them one at a time):

1. **Tool name** — What is the snake_case tool name the LLM will call? (e.g. `send_email`)
2. **Purpose** — One sentence: what does this tool do?
3. **Parameters** — List each parameter: name, type (`string`, `integer`, `boolean`, `number`), required or optional, short description.
4. **Context needed?** — Does the handler need `ToolContext` fields? (`channel`, `chat_id`, `user_id`, `owner_id`). Answer yes/no and which fields.
5. **Multiple tools?** — Does this module expose more than one tool? If yes, list the additional tool names and their parameters.
6. **Config settings** — For a core tool: beyond `enabled: bool`, does it need runtime config (timeout, max size, API key)? For an extension: which settings live in `[extensions.config.<module>]`?
7. **Dependencies** — Does the handler need any injected dependency (e.g. a storage backend, an `EventBus`, a third-party client)? Name it and its type.

Wait for the user's answers before proceeding.

---

## Step 2 — Confirm plan

Summarize what you will generate **for the chosen path**, then ask for confirmation.

Extension path:
- `<module>.py` — a module with `register(mb)` and the tool(s) via `@mb.tool` (or `mb.add_tool`)
- `config.toml` snippet — `[extensions].modules = ["<module>"]` and any `[extensions.config.<module>]` slice

Core path:
- `minibot/llm/tools/<module>.py` — tool class
- `minibot/llm/tools/descriptions/<tool_name>.txt` — LLM-facing description (one per tool name; optional if the schema uses an inline description)
- Config model class name and where it goes in `schema.py` + `ToolsConfig` (only if settings are needed)
- Registration: which bundled extension module (`minibot/extensions/tools/<domain>.py`) gets the `register(mb)` entry, or a wiring edit in `factory.py::build_enabled_tools` for an always-on tool
- `config.example.toml` snippet

---

## Step 3 — Generate files

Read each target file before editing it.

### 3a. Extension path

Write a single `<module>.py`. Follow [references/TOOL_PATTERNS.md](references/TOOL_PATTERNS.md) — "Extension tool (default path)".

Key rules:
- Module-level `def register(mb: ExtensionContext) -> None`.
- Use `@mb.tool` with `async def <tool_name>(args: <Model>, context: ToolContext)`; the function name is the tool name, the docstring is the description, the first argument's pydantic model is the JSON schema.
- Define pydantic argument models **at module scope**, not inside `register()` (postponed annotations cannot resolve them otherwise).
- Use `mb.add_tool(binding)` instead of `@mb.tool` when you need a custom tool name, a hand-written schema, or a description loaded from a file.
- Return `dict[str, Any]` for normal results; raise `ToolInputError` for invalid arguments.
- Settings come from `mb.config` (the `[extensions.config.<module>]` slice); use `mb.settings` for validated application config.
- No inline comments unless a constraint is non-obvious.

### 3b. Core path — tool class

`minibot/llm/tools/<module>.py`, following [references/TOOL_PATTERNS.md](references/TOOL_PATTERNS.md).

Key rules:
- Class name: `PascalCase` matching the module name.
- `bindings(self) -> list[ToolBinding]` returns one `ToolBinding` per tool.
- `_schema(self) -> Tool` (or `_<name>_schema`) builds the `Tool` using `load_tool_description("<tool_name>")` for the description.
- `async _handle(self, payload, context) -> dict[str, Any]` — always async.
- Use schema helpers from `schema_utils.py`: `strict_object`, `string_field`, `integer_field`, `nullable_string`, `nullable_integer`, `nullable_boolean`.
- Use `arg_utils` helpers for input coercion when validating payload fields.
- Return `dict[str, Any]` for normal results; an explicit `ToolResult` only for directives (e.g. sending a file).
- If `ToolContext` is not needed, name the parameter `_`.
- No inline comments unless a constraint is non-obvious.

### 3c. Core path — description file

`minibot/llm/tools/descriptions/<tool_name>.txt`, one `.txt` per tool name. Plain text, no markdown.

Write:
- Line 1: one-sentence summary of what the tool does.
- (Optional) Second paragraph: when to use it, when NOT to use it, any preconditions.
- Final line: what the tool returns.

Keep it concise. The LLM reads this at runtime. Skip this file when the tool is registered via an extension with a docstring description.

### 3d. Core path — config

`minibot/adapters/config/schema.py` — read the file first. Only when the tool needs settings:

1. A new `class <Name>ToolConfig(BaseModel)` with `enabled: bool = False` and any extra fields.
2. A field `<key>: <Name>ToolConfig = <Name>ToolConfig()` inside `ToolsConfig`.

Place the new config class near related tool configs (alphabetical or by similarity).

### 3e. Core path — registration

This is the step that changed. `ToolFeature` and `_OPTIONAL_FEATURES` **no longer exist**
in `llm/tools/factory.py` — do not add them.

Two valid mechanisms:

1. **Bundled extension (default for optional tools)** — add to the right module under
   `minibot/extensions/tools/` (e.g. `utility.py`):

   ```python
   def register(mb: ExtensionContext) -> None:
       if mb.settings.tools.<key>.enabled:
           mb.add_tool(<ClassName>(config=mb.settings.tools.<key>).bindings())
   ```

2. **`build_enabled_tools` (always-on tools only)** — wire directly in
   `minibot/llm/tools/factory.py`, mirroring `CalculatorTool` or `SkillLoaderTool`.

### 3f. Example config snippet

Extension path:

```toml
[extensions]
modules = ["<module>"]

[extensions.config.<module>]
# any extension settings with their defaults
```

Core path:

```toml
[tools.<key>]
enabled = false
# any extra settings with their defaults
```

---

## Step 4 — Lint

Run `poetry run ruff check --fix minibot` and `poetry run ruff format minibot` and report any issues.

---

## Rules

- Never skip a file for the chosen path; extension-path work must not touch `schema.py` or `factory.py`.
- Read each file before editing it.
- Never use regex or string matching for semantic decisions inside tool handlers.
- Do not write tests unless the user asks.
- Do not add docstrings to private methods.
- Line length: 119 characters.