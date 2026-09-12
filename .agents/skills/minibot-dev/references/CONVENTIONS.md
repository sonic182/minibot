# Conventions and Traps

Things enforced by code or CI that are not guessable from the file tree. Read this before your
first edit in the repo.

## What CI actually gates

`.github/workflows/ci.yml` runs exactly three steps on push-to-main and every PR:

```
poetry install --all-extras
poetry run pylint --disable=all --enable=disallowed-name minibot     ← style gate
poetry run pytest
```

**No ruff, no ruff format, no type checker, no coverage floor.** Ruff is a project convention
from `AGENTS.md` — run it anyway (`poetry run ruff check --fix minibot tests`,
`poetry run ruff format .`), but know it will not block a merge.

`.github/workflows/docs.yml` adds a docs gate on main, path-filtered to `docs/**`, `minibot/**`,
`ARCHITECTURE.md`, `README.md`, `config.example.toml`, `poetry.lock`, `pyproject.toml`. It runs
`sphinx-build -W`, so **any new Sphinx warning fails the deploy** — including an RST heading
without a preceding blank line.

### The pylint rule is stricter than it looks

`pyproject.toml:140`:

```
bad-names-rgxs = ["^(?!(?:i|j|x|y|mb|on|ok)$).{1,2}$"]
```

Every 1- or 2-character identifier is banned except `i`, `j`, `x`, `y`, `mb`, `on`, `ok`.
That means `e`, `f`, `fp`, `db`, `kv`, `id`, `ex` all fail CI. This is why the codebase writes
`except … as exc` everywhere. It is checked on `minibot/` only, not `tests/`.

## Style

- Line length **119** (`pyproject.toml`), double quotes, target `py312`, Python 3.12–3.14.
- Ruff rule set: `E, F, I, UP, B, T201`. **`T201` bans `print()`** — use structured logging.
- Imports grouped standard library → third-party → local.
- `snake_case` functions/variables, `PascalCase` classes, explicit type hints.
- Async-first on I/O paths; timezone-aware datetimes; `ValueError` in validators.
- **No incidental comments.** Docstrings are fine where they are a public documentation
  surface (tool descriptions, config models) or where a constraint is genuinely non-obvious.

## Never classify LLM intent by text matching

Do not use regex, substring checks, or ad-hoc text matching to decide what the model meant or
what state it is in. Use structured schema fields or model-native structured output. Text
matching is acceptable only for deterministic protocol parsing — markdown fences, SSE framing.

Nothing enforces this; it is a design rule. The live reference implementation is the tool-use
guardrail: an LLM classifier returning an `extra="forbid"` pydantic payload with a bounded retry
loop and `fail_open=True` — `app/tool_guardrail_validator.py:11` and
`app/tool_use_guardrail.py:41`. Even the e2e suite grades answers through a schema-constrained
LLM call rather than string matching.

(`AGENTS.md` cites `should_answer_to_user` as the example field. That field no longer exists
anywhere in the codebase — use the guardrail payload as the model instead.)

## Tool-scoping has two different semantics

This is the single most violable invariant in the codebase.

- **Main agent** — `app/tool_capabilities.py` uses `apply_allow_deny` over the whole binding
  list, so patterns apply uniformly to local and MCP names alike.
- **Specialist agents** — `app/agent_policies.py:19` uses `filter_tools_for_agent`, which
  special-cases MCP:
  - MCP tools are gated by **`mcp_servers` membership plus deny patterns only**.
    `tools_allow` is *never consulted* for an MCP name. An agent with
    `tools_allow: ["mcp_foo__*"]` and no `mcp_servers` gets **zero** MCP tools.
  - With **neither** `tools_allow` nor `tools_deny` set, the agent gets **zero non-MCP tools**
    (`agent_policies.py:48`) — not "all tools", which is the intuitive reading.

`tools_allow` and `tools_deny` are mutually exclusive; this is enforced in `tool_policy_utils.py`
and again by pydantic validators in `adapters/config/schema.py`.

Reserved names always stripped from delegated agents (`agent_policies.py:10`): `invoke_agent`,
`fetch_agent_info`, `spawn_task`, `list_tasks`, `cancel_task` — so there is no recursive
delegation.

One tool escapes the system entirely: `pre_response` is prepended by `AgentRuntime` to every
runtime's tool list, bypassing both the factory and allow/deny filtering. Listing it in
`tools_allow` is a no-op.

## Tool names are unique *after* alias canonicalization

`llm/tools/factory.py:95` (`_ensure_unique_tool_names`) keys its dedupe map on the canonical
name. The alias table lives at `llm/services/tool_executor.py:29`:

```
http_client     → http_request
calculator      → calculate_expression
datetime_now    → current_datetime
artifact_insert → self_insert_artifact
```

Naming a new tool `http_client`, `calculator`, `datetime_now` or `artifact_insert` raises
`duplicate tool name detected` at startup even though no tool literally carries that name.

## The MCP name-prefix trap

`app/mcp_tool_name.py` detects an MCP tool with a hardcoded `name.startswith("mcp_")` plus a
`"__"` separator, while the prefix that *generates* those names is user-configurable
(`adapters/config/schema.py:574`, `name_prefix: str = "mcp"`).

Changing `name_prefix` away from `"mcp"` silently reclassifies every remote tool as a local
tool, which breaks specialist tool scoping and `exclusive_mcp` ownership mode. Corollary: an
MCP server name may not contain `__`.

## Tool descriptions are sidecar files

`llm/tools/description_loader.py` reads `<tool_name>.txt` from the tool's package. An **empty**
file raises `ValueError`; a **missing** file raises `FileNotFoundError` — both at build time,
aborting startup. Packaging depends on the `minibot/**/*.txt` glob in `pyproject.toml`, so a
description living outside `minibot/` ships broken.

Extension tools skip all of this: the function docstring is the description.

## Bundled extensions are implicit

Eleven modules load automatically and appear nowhere in `config.toml`
(`app/extensions.py:26`): rag, mcp, rabbitmq, scheduler, tasks, and the tool modules
execution / media / memory / network / utility / workspace. Telegram is appended for the
`daemon` entrypoint only. **Workers load none of them.**

Reading `[extensions].modules` and concluding "no extensions are active" is wrong.

## Tests

`pytest.ini`:

- `asyncio_mode = strict` — an async test without `@pytest.mark.asyncio` **silently does not
  run**. This is the easiest way to ship a test that proves nothing.
- `python_files = tests/test_*.py` — a path-shaped pattern, not the default basename pattern.
- `addopts` always collects coverage but with `--cov-fail-under=0`; coverage is reported,
  never gated.
- Markers: `e2e`, `timeout`.

The `e2e` suite is collected by CI and stays green only because of a module-level skip guard
requiring `E2E_RUN` plus both `OPENAI_API_KEY` and `OPENROUTER_API_KEY`. Removing that guard
would make CI call paid APIs.

Write as few tests as possible while covering the important paths; prefer integration and
functional tests over granular unit tests. Use the `minibot-testing` skill.

## Config has no `${ENV}` interpolation

`config.toml` is parsed with plain `tomllib`. The only environment variable read anywhere in
config loading is `MINIBOT_CONFIG` (`adapters/config/loader.py:12`). A literal
`api_key = "${OPENAI_API_KEY}"` is passed through as that exact string.

The convention is to store the variable *name* and resolve it in the consuming code:

```toml
[extensions.config.my_module]
token_env = "GITHUB_TOKEN"
```

```python
token = os.environ.get(mb.config.get("token_env", ""))
```

## Skill frontmatter (MiniBot's own parser)

MiniBot implements agentskills.io but reads it more strictly than the spec
(`app/skill_definitions_loader.py`):

| | Spec | MiniBot |
|---|---|---|
| `description` | ≤1024 chars | warns above **300** (`:14`) |
| other fields | `license`, `metadata`, `compatibility`, `allowed-tools` allowed | parsed out and ignored, never rejected (`:156`) |
| frontmatter syntax | YAML | flat `key: value` only; indented lines skipped |
| body | any | **must be non-empty** or the skill is dropped |

`enabled: false` is a MiniBot extension that hides a skill from the runtime; other clients
ignore it. `name` must match the directory name. Keep `SKILL.md` under 500 lines and push
detail into `references/`, one level deep.

## Orphan config

`.jscpd.json` sets a 1% duplicate-code threshold over `minibot` and `tests`, but nothing
references it — no Makefile target, no workflow, no `package.json`. It only runs if someone
invokes `npx jscpd` by hand.

## Git

**Never run git write commands** — no `add`, `commit`, `push`, or any other mutation. Edit
files directly; the user handles commits.
