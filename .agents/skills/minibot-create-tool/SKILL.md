---
name: minibot-create-tool
description: Guided wizard for adding a new LLM tool to Minibot. Asks targeted questions then generates all required files: tool class, description .txt, config schema entry, and factory registration. Use when the user wants to add a new tool to minibot/llm/tools/.
---

# Minibot Create Tool

Guided wizard that collects tool requirements then generates all files needed to add a new LLM tool to Minibot.

## Workflow

### Step 1 — Gather requirements

Ask the user these questions **in one message** (do not ask them one at a time):

1. **Tool name** — What is the snake_case tool name the LLM will call? (e.g. `send_email`)
2. **Purpose** — One sentence: what does this tool do?
3. **Parameters** — List each parameter: name, type (`string`, `integer`, `boolean`, `number`), required or optional, short description.
4. **Context needed?** — Does the handler need `ToolContext` fields? (`channel`, `chat_id`, `user_id`, `owner_id`). Answer yes/no and which fields.
5. **Multiple tools?** — Does this class expose more than one tool? If yes, list the additional tool names and their parameters.
6. **Config settings** — Beyond `enabled: bool`, does this tool need runtime config (e.g. timeout, max size, API key)? List name, type, default.
7. **Dependencies** — Does this tool need any injected dependency (e.g. `MemoryBackend`, `LocalFileStorage`, `EventBus`, a third-party client)? If yes, name it and its type.

Wait for the user's answers before proceeding.

---

### Step 2 — Confirm plan

Summarize what you will generate:
- `minibot/llm/tools/<module>.py` — tool class
- `minibot/llm/tools/descriptions/<tool_name>.txt` — LLM-facing description (one per tool)
- Config model class name and where it goes in `schema.py` + `ToolsConfig`
- Factory builder name and `ToolFeature` entry in `factory.py`
- `config.example.toml` snippet

Ask for confirmation or corrections before writing any file.

---

### Step 3 — Generate files

Generate all files in order. Read each target file before editing it.

#### 3a. Tool class — `minibot/llm/tools/<module>.py`

Follow [references/TOOL_PATTERNS.md](references/TOOL_PATTERNS.md) exactly.

Key rules:
- Class name: `PascalCase` matching the module name.
- `bindings(self) -> list[ToolBinding]` returns one `ToolBinding` per tool.
- `_schema(self) -> Tool` (or `_<name>_schema`) builds the `Tool` object using `load_tool_description("<tool_name>")` for the description.
- `async _handle(self, payload, context) -> dict[str, Any]` — always async.
- Use schema helpers from `schema_utils.py`: `strict_object`, `string_field`, `integer_field`, `nullable_string`, `nullable_integer`, `nullable_boolean`.
- Use `arg_utils` helpers for input coercion when validating payload fields.
- Return `dict[str, Any]` for normal results. Return `ToolResult` only when a directive (e.g. sending a file) is needed.
- If `ToolContext` is not needed, name the parameter `_` in the handler signature.
- No inline comments unless a constraint is non-obvious.

#### 3b. Description file — `minibot/llm/tools/descriptions/<tool_name>.txt`

One `.txt` file per tool name. Plain text, no markdown.

Write:
- Line 1: one-sentence summary of what the tool does.
- (Optional) Second paragraph: when to use it, when NOT to use it, any preconditions.
- Final line: what the tool returns.

Keep it concise. The LLM reads this at runtime.

#### 3c. Config — `minibot/adapters/config/schema.py`

Read the file first. Add:
1. A new `class <Name>ToolConfig(BaseModel)` with `enabled: bool = False` and any extra fields.
2. A field `<key>: <Name>ToolConfig = <Name>ToolConfig()` inside `ToolsConfig`.

Place the new config class near related tool configs (alphabetical or by similarity).

#### 3d. Factory — `minibot/llm/tools/factory.py`

Read the file first. Make three edits:

1. **Import** — add `from minibot.llm.tools.<module> import <ClassName>` with other tool imports (alphabetical).
2. **Builder function** — add `_build_<key>_feature(context, _)` following the pattern of existing builders.
3. **ToolFeature entry** — add to `_OPTIONAL_FEATURES` tuple in a sensible position:
   ```python
   ToolFeature(
       key="<key>",
       labels=("<tool_name>",),
       enabled_in_config=lambda settings: _tool_enabled(settings, "<key>"),
       builder=_build_<key>_feature,
   ),
   ```
   If the tool has multiple tool names, list all in `labels`.

#### 3e. Example config snippet

Show a `config.example.toml` snippet the user can copy:
```toml
[tools.<key>]
enabled = false
# any extra settings with their defaults
```

---

### Step 4 — Lint

Run `poetry run ruff check --fix minibot` and `poetry run ruff format minibot` and report any issues.

---

## Rules

- Never skip a file. All four touch points (class, description, config, factory) must be generated.
- Read each file before editing it.
- Never use regex or string matching for semantic decisions inside tool handlers.
- Do not write tests unless the user asks.
- Do not add docstrings to private methods.
- Line length: 119 characters.
