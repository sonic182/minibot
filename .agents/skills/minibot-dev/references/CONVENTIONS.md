# Conventions and Traps

Things enforced by code or CI that are not guessable from the file tree. Style, lint and
output-classification rules live in `AGENTS.md`; test-suite traps live in the `minibot-testing`
skill.

## What CI actually gates

`.github/workflows/ci.yml` runs these steps on push-to-main and every PR:

```
poetry install --all-extras
poetry run pylint --disable=all --enable=disallowed-name minibot     ← style gate
poetry run pytest
npx --yes jscpd@4.2.3 --config .jscpd.json                            ← duplication gate
```

**No ruff, no ruff format, no type checker, no coverage floor.** Run ruff anyway (`AGENTS.md`),
but know it will not block a merge. `.jscpd.json` scans Python under `minibot/` and `tests/`
with `threshold: 3`, so copy-pasted blocks in either fail CI.

`.github/workflows/docs.yml` builds the docs on PRs and pushes to main, path-filtered to
`docs/**`, `minibot/**`, `README.md`, `config.example.toml`, `poetry.lock`, `pyproject.toml`; it
deploys only on push. It runs `sphinx-build -W`, so **any new Sphinx warning fails the build** —
including an RST heading without a preceding blank line, or a config-model docstring that is
not valid RST (config models are rendered with `autoclass`).

### The pylint rule is stricter than it looks

`[tool.pylint.basic]` in `pyproject.toml`:

```
bad-names-rgxs = ["^(?!(?:i|j|x|y|mb|on|ok)$).{1,2}$"]
```

Every 1- or 2-character identifier is banned except `i`, `j`, `x`, `y`, `mb`, `on`, `ok`.
That means `e`, `f`, `fp`, `db`, `kv`, `id`, `ex` all fail CI. It is checked on `minibot/` only,
not `tests/`.

## Never classify LLM intent by text matching

The rule and its provider-error and Telegram exceptions are in `AGENTS.md`. Nothing enforces
it; it is a design rule. The live reference implementation is the tool-use guardrail: an LLM
classifier returning an `extra="forbid"` pydantic payload with a bounded retry loop and
`fail_open=True` — `ToolGuardrailPayload` in `app/tool_guardrail_validator.py` and
`LLMClassifierToolUseGuardrail` in `app/tool_use_guardrail.py`. The e2e suite also grades
answers through an LLM classifier call (`_classify_youtube_answer`) rather than string matching.

## Tool-scoping has two different semantics

This is the single most violable invariant in the codebase.

- **Main agent** — `main_agent_tool_view` (`app/tool_capabilities.py`) uses `apply_allow_deny`
  over the whole binding list, so patterns apply uniformly to local and MCP names alike.
- **Specialist agents** — `filter_tools_for_agent` (`app/agent_policies.py`) special-cases MCP:
  - MCP tools are gated by **`mcp_servers` membership plus deny patterns only**.
    `tools_allow` is *never consulted* for an MCP name. An agent with
    `tools_allow: ["mcp_foo__*"]` and no `mcp_servers` gets **zero** MCP tools.
  - With **neither** `tools_allow` nor `tools_deny` set, the agent gets **zero non-MCP tools**
    — not "all tools", which is the intuitive reading.

`tools_allow` and `tools_deny` are mutually exclusive; this is enforced by `validate_allow_deny`
(`app/tool_policy_utils.py`) and again by pydantic validators in `config/schema.py`.

Reserved names always stripped from delegated agents (`RESERVED_DELEGATION_TOOL_NAMES` in
`app/agent_policies.py`, applied by `strip_reserved_delegation_tools`): `fetch_agent_info`,
`spawn_task`, `delegate`, `list_tasks`, `get_task`, `cancel_task` — so there is no recursive
delegation.

One tool escapes the system entirely: `pre_response` is prepended by `AgentRuntime`
(`app/agent_runtime.py`) to every runtime's tool list, bypassing both the factory and
allow/deny filtering. Listing it in `tools_allow` is a no-op.

## Tool names are unique *after* alias canonicalization

`_ensure_unique_tool_names` (`app/tool_factory.py`) keys its dedupe map on
`canonical_tool_name`. The alias table is `_TOOL_NAME_ALIASES` in `llm/services/tool_executor.py`:

```
http_client     → http_request
calculator      → calculate_expression
datetime_now    → current_datetime
artifact_insert → self_insert_artifact
```

Naming a new tool `http_client`, `calculator`, `datetime_now` or `artifact_insert` raises
`duplicate tool name detected` at startup even though no tool literally carries that name.

## The MCP name-prefix trap

`is_mcp_tool_name` and `extract_mcp_server` (`app/mcp_tool_name.py`) detect an MCP tool by
`<prefix>_` plus a `"__"` separator. The prefix is user-configurable (`[tools.mcp].name_prefix`,
default `"mcp"`), and both helpers default to `DEFAULT_MCP_NAME_PREFIX` when no prefix is passed.

Any caller that classifies a tool name as MCP must pass the configured prefix, as
`filter_tools_for_agent`, `main_agent_tool_view` and `ManagedAgentPolicy` do. Relying on the
default silently reclassifies every remote tool as local under a custom `name_prefix`, which
breaks specialist tool scoping and `exclusive_mcp` ownership mode. Corollary: the server name
is everything up to the first `__`, so an MCP server name containing `__` is misparsed.

## Tool descriptions are sidecar files

`load_tool_description` (`llm/tools/description_loader.py`) reads `<tool_name>.txt` from a
package — `minibot.llm.tools` by default, or the caller's own package when it passes
`package=__spec__.parent` (as `extensions/tools/agent_management.py` does). An **empty** file
raises `ValueError`; a **missing** file raises `FileNotFoundError` — both at build time,
aborting startup. Packaging depends on the `minibot/**/*.txt` glob in `pyproject.toml`, so a
description living outside `minibot/` ships broken.

`mb.tool` extension tools skip all of this: the function docstring is the description.

## Bundled extensions are implicit

Fifteen modules load automatically for the daemon and console and appear nowhere in
`config.toml` (`_bundled_modules` in `app/extensions.py`): the integrations decision, rag, mcp,
rabbitmq; the services scheduler, tasks; and the tool modules agent_management, codemode,
execution, media, memory, network, utility, vault, workspace. Telegram is prepended for the
`daemon` entrypoint only. **Workers load only the scheduler and utility bundles**, plus
kv_memory / rag / codemode when listed in `[tasks] worker_extensions` (`WORKER_OPT_IN_MODULES`).
`extensions/tools/graph.py` is the exception: opt-in through `[extensions].modules`.

Reading `[extensions].modules` and concluding "no extensions are active" is wrong.

## Config environment and secret interpolation

`Settings.from_dict()` expands `${ENV}` string values after TOML parsing and before
normalization and validation, including nested lists, tables, and extension configuration
(`expand_environment` in `config/environment.py`). Missing variables fail loading even in
disabled sections; `$${ENV}` escapes a literal reference. Expansion is single-pass and does not
load `.env` files.

`${secret:NAME}` resolves in a second pass from the unlocked vault (`AppContainer.configure`,
`adapters/container/app_container.py`). It requires `[vault] enabled = true` and is rejected
inside `[vault]` itself.

The configurator (`adapters/config/configurator.py`) must preserve both kinds of reference on
disk while using resolved values at runtime. Direct `Settings(...)` and
`Settings.model_validate(...)` calls expand nothing. Existing `token_env = "GITHUB_TOKEN"` fields
still resolve names in their consuming code. `MINIBOT_CONFIG` selects the config path
(`resolve_config_path` in `config/paths.py`). Full contract: `docs/config.rst`.

## Skill frontmatter (MiniBot's own parser)

MiniBot implements agentskills.io but reads it differently from the spec
(`parse_skill_file` / `parse_skill_frontmatter` in `app/skill_definitions_loader.py`):

| | Spec | MiniBot |
|---|---|---|
| `name` | must match the directory | not checked; only warns when it fails `_NAME_RE` |
| `description` | ≤1024 chars | logs above **300** (`_DESCRIPTION_MAX_CHARS`) — keep it under |
| `compatibility` | ≤500 chars, free text | returned by `activate_skill`; the model reports a missing tool instead of improvising |
| other fields | `license`, `metadata`, `allowed-tools` allowed | parsed out and ignored, never rejected |
| frontmatter syntax | YAML | flat `key: value` plus block scalars; nested keys and list items skipped |
| body | any | **must be non-empty** or the skill is dropped |

Follow the spec anyway (`name` = directory), since other hosts enforce it. A skill is on while
its directory is in a discovery path; bundled skills are switched off with
`[tools.skills] native_disabled`.
