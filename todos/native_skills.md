# Native skills — working proposal

Detail document for **Phase 2** of [`ROADMAP.md`](ROADMAP.md).
Status: PR 1 shipped (#82); PR 2 in review (#83); PR 3 (`reload_agents`) is next.

## Context

Skills today are discovered only from the filesystem (`./.agents/skills`, `./.claude/skills`,
`~/.agents/skills`, `~/.claude/skills`), so a fresh MiniBot install ships with **zero** skills
and the `[tools.skills]` feature looks empty until the user authors one by hand. There is also
no in-band way for the agent to learn *where* it may write a new skill — `activate_skill`
returns the `skill_dir` of an existing skill, nothing else.

Goal: ship a small set of skills inside the package (`minibot/skills/`), gated by
`[tools.skills] enabled` and individually switchable. The v1 set is four skills:
`create-skill`, `import-skill`, `minibot-docs`, `create-agent`.

## Current wiring (what we are extending)

```
AppContainer.configure()                          adapters/container/app_container.py:58
  └─ SkillRegistry(paths=settings.tools.skills.paths or None)    app/skill_registry.py:15
      ├─ resolve_skill_discovery_paths()          app/skill_definitions_loader.py:30
      │    └─ _default_discovery_paths()          app/skill_definitions_loader.py:36
      │         → [(cwd/.agents/skills, True), (cwd/.claude/skills, True),
      │            (home/.agents/skills, False), (home/.claude/skills, False)]
      ├─ fingerprint_skill_paths()                app/skill_definitions_loader.py:88   ← hot reload
      └─ load_skill_specs() → _load_from_paths()  app/skill_definitions_loader.py:47
           └─ _parse_skill_file() → SkillSpec     core/skills.py:8

Dispatcher.__init__                               app/dispatcher.py:57
  └─ build_enabled_tools(skill_registry=...)      llm/tools/factory.py:57
      └─ if settings.tools.skills.enabled → SkillLoaderTool(...)  llm/tools/skill_loader.py:19
  └─ build_llm_turn_service(...)                  app/dispatcher.py:108
      └─ PromptService._skill_catalog_fragment()  app/handlers/services/prompt_service.py:151
```

Two properties we rely on and must not break:

- The registry hot-reloads on an mtime/size fingerprint (`skill_registry.py:55`), so a skill
  written at runtime is visible on the very next `list_skills` — no restart.
- Setting `[tools.skills] paths` **replaces** the defaults entirely (`skill_definitions_loader.py:31`).
  Native skills must therefore live on their own tier, not inside that list, or configuring
  `paths` would silently delete them.

## Delivery plan

Ordered by impact, constrained by dependencies. Each PR is independently shippable and leaves
the tree working; tests and docs ride along with the PR that introduces the behaviour rather
than trailing behind it.

| # | PR | Impact | Depends on |
|---|---|---|---|
| ~~1~~ | ~~Single-source the package version~~ | **Done — merged in #82** | — |
| 2 | Native skill tier + `create-skill` | Turns an empty feature on for every install | 1 |
| 3 | `reload_agents` tool | Specialists written at runtime become usable without a restart | — |
| 4 | `get_settings` tool | Stops the agent guessing its own configuration | 1, 2 |
| 5 | `minibot-docs` skill | The bot can answer questions about itself | 4 |
| 6 | `install-skill` skill + `install_skill` tool | Growth path for skills; riskiest surface | 2 |
| 7 | `create-agent` skill | Narrowest audience; pure markdown once 3 is in | 2, 3 |

PR 3 goes first after PR 2: it is the only remaining real code change in the agent area, it is
independent of every skill, and `create-agent` is not worth shipping until it exists. PRs 5, 6
and 7 are independent of each other and can land in any order once their dependencies are in.

---

## PR 1 — Single-source the package version ✅ DONE (#82)

Merged. Kept here because PR 4 builds directly on what it added.

**What shipped**

- `minibot/__init__.py` reads `importlib.metadata.version("minibot")`, falling back to `0.0.0`.
- `build_environment_prompt_fragment` (`app/environment_context.py:10`) now emits
  `- MiniBot version:` and `- Config file:`, the latter saying `(built-in defaults in use)` when
  no file was found. It gained an optional `config_path` argument.
- **Beyond the original plan:** `AppContainer.get_config_path()` records the path
  `configure()` actually resolved, and `Dispatcher` / `build_enabled_tools` thread it into the
  fragment. Without it `minibot console --config other.toml` would report the recomputed default
  instead of the file really loaded. **PR 4 should read `config_path` from this getter rather
  than calling `resolve_config_path()` again.**
- `minibot --version` (`app/daemon.py:161`); there was no version flag before.
- `tests/test_environment_context.py` covers the reported version/path and the missing-file case.

**One caveat to remember:** `importlib.metadata` reads *installed* distribution metadata, so in
an editable checkout the version lags a `pyproject.toml` bump until the next `poetry install`.
Correct for real installs; `docs/conf.py:12` still reads `pyproject.toml` directly and is exact.

<details>
<summary>Original plan</summary>

**Impact:** small diff, real bug, no dependencies, and everything in PR 4 and 5 that reports
"what am I running" is wrong until it lands. Hence first.

`minibot/__init__.py:1` declares `__version__ = "0.1.0"` while `pyproject.toml:3` says
`0.16.0`. The constant is referenced nowhere else in the codebase, which is why the drift went
unnoticed.

Use the pattern from `aiosonic` (`~/sandbox/proyectos/aiosonic/aiosonic/version.py`) — read the
installed distribution metadata rather than hardcoding:

```python
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("minibot")
except PackageNotFoundError:
    __version__ = "0.0.0"
```

**Changes**

- `minibot/__init__.py` — the snippet above.
- `app/environment_context.py:9` — surface the version and the resolved config path
  (`adapters/config/loader.py:11`, `resolve_config_path`) in the prompt fragment, next to the
  cwd and managed-root lines already there.
- `app/daemon.py:main` — a `--version` flag; there is none today. Optional, but this is the
  cheapest moment for it.
- `docs/conf.py:12` reads the version straight out of `pyproject.toml`; unaffected, leave it.

**Done when** `python -c "import minibot; print(minibot.__version__)"` matches `pyproject.toml`
in an editable install, and falls back to `0.0.0` rather than raising when the distribution
metadata is absent.

</details>

---

## PR 2 — Native skill tier + `create-skill`  ← in review (#83)

**Impact:** the load-bearing PR. Everything else in this document is a file drop on top of it,
and on its own it turns `[tools.skills]` from an empty feature into one that ships something.

`create-skill` rides along rather than waiting for its own PR: it is the first inhabitant of
the new tier and the thing that proves the tier works end to end.

### 2a. Where the files live

```
minibot/skills/
  create-skill/
    SKILL.md
```

Packaging: `packages = [{ include = "minibot" }]` already ships every `.py`, so helper scripts
(PR 6) ride along. Markdown does **not** — add to `pyproject.toml:31`:

```toml
{ path = "minibot/skills/**/*.md", format = ["sdist", "wheel"] },
```

(The `minibot/**/*.txt` entry that ships tool descriptions is the precedent.)

### 2b. A third discovery tier, with an explicit source

`SkillSpec` gains a `source` field, and the loader's `(Path, bool is_project_level)` tuple
becomes `(Path, SkillSource)`. This replaces a bool that already could not express three tiers.

| rank | source | paths |
|---|---|---|
| 0 | `project` | `paths` if set, else `./.agents/skills`, `./.claude/skills` |
| 1 | `user` | `~/.agents/skills`, `~/.claude/skills` (skipped when `paths` is set) |
| 2 | `native` | `minibot/skills/` — appended last, **always**, independent of `paths` |

Precedence stays "lower rank wins", so a user-authored `create-skill` shadows the bundled one —
that is the intended override path, and it deserves its own log line
(`skill_definitions_loader.py:64-84` already has the collision-warning block).

Files: `minibot/core/skills.py:8`, `minibot/app/skill_definitions_loader.py:30,36,47`.

### 2c. Config

```toml
[tools.skills]
enabled = true
preload_catalog = true
# paths = ["./skills"]

# Bundled skills shipped inside the minibot package. Independent of `paths`.
native = true
# Opt out of individual bundled skills by name.
native_disabled = ["import-skill"]
# Where the agent creates or imports new skills. Auto-added as the top-priority
# discovery path. Default: first `paths` entry, else "./.agents/skills".
# write_path = "./skills"
```

`native` is a master switch and `native_disabled` an opt-out list, so the default (all bundled
skills on) needs no config at all, and turning one off is one line. Filtering happens after
parsing, keyed on `spec.source == "native"`.

Files: `SkillsToolConfig` at `minibot/adapters/config/schema.py:673`; `config.example.toml:518`;
`docs/config.rst:380` (autoclass picks up the docstring — keep the `TOML section` convention).

### 2d. Telling the agent where its skill path is

Extend the `list_skills` result (`minibot/llm/tools/skill_loader.py:76`) with:

```json
{
  "write_dir": "/abs/path/to/skills",
  "write_dir_access": "filesystem" | "bash",
  "discovery_paths": ["/abs/...", "..."],
  "matches": [{"name": "...", "description": "...", "source": "project|user|native"}]
}
```

`write_dir_access` is the one non-obvious bit. The `filesystem` tool is hard-scoped to
`[tools.file_storage] root_dir` with `allow_outside_root = false`
(`minibot/llm/tools/file_storage.py`, `schema.py:642`), so if `write_path` sits outside that
root the agent **cannot** write a skill with `filesystem` and must use `bash`. Computing that
once, deterministically, beats letting the model discover it by failing a tool call.

`SkillRegistry` grows `write_dir() -> Path` alongside the existing `discovery_paths()`
(`skill_registry.py:52`) so the tool does not re-resolve config — and so PR 4 can read the same
value rather than computing it twice.

### 2e. `create-skill` (SKILL.md, no code)

Body covers, in this order:

1. Call `list_skills` first; `write_dir` in the response is where the skill goes, and
   `write_dir_access` says which tool to write with.
2. Layout: `<write_dir>/<skill-name>/SKILL.md`; directory name **must** equal frontmatter `name`.
3. MiniBot's parser is stricter than agentskills.io — this is the part a generic
   skill-writing guide gets wrong (see `.agents/skills/minibot-dev/references/CONVENTIONS.md`):
   - frontmatter is flat `key: value` only, not real YAML; indented lines are skipped
   - only `name`, `description`, `enabled` are read; everything else is ignored, not rejected
   - `description` warns above 300 chars
   - an empty body silently drops the skill
4. Write a description that says *when* to use the skill, not just what it is — it is the only
   thing in the prompt catalog.
5. Keep SKILL.md under ~500 lines; push detail into `references/`, one level deep.
6. Verify by calling `list_skills` again (the registry hot-reloads; no restart needed).

**Tests** (extend `tests/test_skill_registry_and_tools.py`, integration-shaped per the repo's
few-tests rule): native tier loads with `native = true` and disappears with `native = false`;
`native_disabled` removes one skill and leaves the others; a project-level skill of the same
name shadows the native one; `list_skills` reports `write_dir` and `source`.

**Docs:** `config.example.toml`, `docs/config.rst`, and `docs/extending.rst` — which lists
`prompts/ · skills/ · agents/*.md` as the customization surface, and native skills change what
"skills" means there. The docs CI gate runs `sphinx-build -W`, so a new warning fails the deploy.

**Done when** a fresh install with `[tools.skills] enabled = true` and no other config shows
`create-skill` in `list_skills`, and a skill written to `write_dir` at runtime appears on the
next call without a restart.

---

## PR 3 — `reload_agents` tool

**Impact:** the one real code change in the agent area, pulled out of the old `create-agent` PR
and landed first so the skill that depends on it is a pure file drop. Useful on its own too: an
owner who hand-edits `agents/*.md` no longer has to restart the daemon.

**The problem.** `SkillRegistry.refresh_if_stale()` (`app/skill_registry.py:55`) re-reads
skills on an mtime/size fingerprint; `AgentRegistry` (`app/agent_registry.py:6`) has no
equivalent. Specs are loaded once in `AppContainer.configure()`
(`adapters/container/app_container.py:53`) and swapped once more by token auto-config
(`app_container.py:224`, via `replace_all()` at `agent_registry.py:13`); nothing re-reads
`agents/*.md` afterwards. A freshly written specialist is invisible until restart.

**Chosen shape: an explicit tool, not a fingerprint hot reload.** `reload_agents` re-runs
`load_agent_specs(settings.orchestration.directory)` (`app/agent_definitions_loader.py:18`) and
swaps the result in with `replace_all()`. Why a tool rather than mirroring the skill registry:

- **Errors reach the agent.** The loader *raises* on a bad file (invalid frontmatter, empty
  body — `agent_definitions_loader.py:30-38`). A silent background reload would have to swallow
  that; a tool returns it as the result, so the agent that just wrote the file sees what is
  wrong and fixes it in the same turn. That is exactly what `create-agent` needs.
- **The old roster survives a failure.** Swap only on success; on error the registry is left
  untouched and the result says so.
- **Deterministic.** The roster changes when asked, not on whichever call happens to stat the
  directory, and nothing stats the directory on every `get()`.

```json
{"ok": true, "names": ["browser_agent", "researcher"], "added": ["researcher"], "removed": []}
{"ok": false, "error": "agents/researcher.md: agent body prompt cannot be empty"}
```

`replace_all()` keeps the registry's identity, so `AgentDelegateTool` and `PromptService` —
both of which hold the registry, not a snapshot — pick the change up with no further wiring.

**Three things to get right:**

- **`fetch_agent_info` must exist even when the roster started empty.** `build_enabled_tools` only
  builds `AgentInfoTool` when `not agent_registry.is_empty()` (`llm/tools/factory.py:67`).
  Starting with zero agents and reloading one in would leave nothing able to inspect it.
  Drop the emptiness gate (the tool already handles an unknown name) or build it whenever
  `reload_agents` is built. `spawn_task` itself is unaffected: it comes from the tasks extension
  and is gated on `[tasks].enabled`, not on the roster.
- **Token auto-config.** Startup runs `apply_runtime_token_autoconfig_async`
  (`app/token_limits_autoconfig.py:20`) over the specs before the swap; it fetches the models.dev
  catalog. Reloaded specs need the same treatment or a reloaded agent runs with unadjusted
  limits — decide in the PR whether to re-run it (network call per reload) or cache the catalog.
- **Not for specialists.** Add `reload_agents` to `RESERVED_DELEGATION_TOOL_NAMES`
  (`app/agent_policies.py:10`); a delegated agent must not rewrite the roster it was picked from.
  Task workers build their own registry (`adapters/tasks/worker.py:325`) per run, so they see new
  files anyway — no worker wiring.

One consequence to accept: the specialist roster is part of the system prompt
(`PromptService._specialist_roster_fragment:134`), so a reload that changes it changes the
prompt fingerprint and drops the cached `previous_response_id` (`session_state_service.py:54-66`)
for that session. Rare and harmless, but deliberate: anything that varies the system prompt pays
this cost.

Files: `minibot/llm/tools/agent_reload.py` (`.bindings()`), `minibot/llm/tools/reload_agents.txt`
(description sidecar), one branch in `factory.py`, the reserved-name addition.

**Tests:** write a new `agents/*.md` after startup, call `reload_agents`, assert it is
delegatable; a broken file returns `ok: false` and leaves the previous roster in place.

**Docs:** `docs/agents.rst` (the page currently implies a restart), `docs/tools.rst`.

**Done when** a specialist written at runtime is delegatable in the same session after one
`reload_agents` call, including from a zero-agent start, and its roster line appears in the next
system prompt.

---

## PR 4 — `get_settings`

**Impact:** independent of any skill, benefits every turn where the agent would otherwise
invent its own configuration, and is the prerequisite that makes PR 5 honest.

A small always-on core tool answering "what am I actually running?".

Sibling to copy: `chat_history_info` (`llm/tools/chat_memory.py:26`) — read-only, no
configuration knob, built unconditionally in `build_enabled_tools` (`llm/tools/factory.py:47`).

Files: `minibot/llm/tools/settings_info.py` (`.bindings()`), `minibot/llm/tools/get_settings.txt`
(the description sidecar — an empty or missing file aborts startup), one branch in `factory.py`.

**Shape: enabled-only.** The response contains a section *only* when that feature is on, so
absence is itself the answer and the payload stays small. No `"enabled": false` noise.

```json
{
  "ok": true,
  "version": "0.16.0",
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

**Allowlist, never a denylist**, and it starts small. Each section names the exact fields it
emits; anything not named is not emitted. A denylist leaks the next token field someone adds to
`schema.py`, and `Settings` is full of them: `channels.telegram.bot_token:145`, `llm.api_key:289`,
`providers.<name>.api_key:349`, `providers.<name>.auth_path`, `tools.mcp.servers[].auth_secret:629`,
`http.basic_auth_password` / `http.auth_token`, plus the RabbitMQ and Qdrant URLs, which carry
credentials inline.

`is_sensitive_argument_key` (`llm/services/tool_executor.py:138`) already exists, but it is a
key-substring denylist built for *log* sanitizing — the wrong shape for a surface the model
reads. Cite it, do not reuse it. The governing rule is already written down for extensions:
"never expose one to the LLM" (`app/extensions.py:75`, on the vault).

`${ENV}` and `${secret:}` are resolved before validation (`schema.py:957`), so by the time the
tool sees `Settings` there is no reference left to show — another reason the emitted set must
be chosen field by field.

### v1 allowlist (the whole of it)

| emitted | source | why the agent needs it |
|---|---|---|
| `version`, `config_path`, `cwd` | `minibot.__version__`, `AppContainer.get_config_path()`, `Path.cwd()` | "what am I running / where from" (all three landed in PR 1) |
| `channels` | **names only** of enabled channels | which surface it is talking on |
| `llm.provider`, `llm.model` | `LLMMConfig.provider/model` | self-description, cost/capability questions |
| `llm.prompts_dir` | `LLMMConfig.prompts_dir` | where prompt packs live |
| `memory.backend`, `max_history_messages`, `max_history_tokens` | `MemoryConfig` | history/compaction questions |
| `agents.directory`, `agents.names` | `OrchestrationConfig.directory`, `AgentRegistry.names()` | required by `create-agent` (PR 7) |
| `tools.<name>` present-or-absent | `ToolsConfig` | which capabilities exist this turn |
| `tools.file_storage.root_dir`, `.mode`, `.max_write_bytes` | `FileStorageToolConfig` | already in the prompt fragment; here in structured form |
| `tools.skills.paths`, `.write_dir`, `.write_dir_access`, `.native`, `.count` | `SkillRegistry` (PR 2d) | required by `create-skill` / `import-skill` |
| `tools.bash.default_timeout_seconds`, `.max_timeout_seconds`, `.env_allowlist` | `BashToolConfig` | timeouts it will otherwise guess; env var **names** (not values) that survive into a command |
| `tools.mcp.servers` | **names only** | which remote toolsets exist |
| `tasks.backend` | `TasksConfig.backend` | async delegation questions |
| `scheduler` | presence only | whether scheduling is available |
| `vault.unlocked` | bool | whether `${secret:}` resolution is live |

### Never emitted, in any form

`llm.api_key`, `llm.base_url`, `llm.extra_headers`, `llm.auth_path`, every `providers.<name>.*`,
`channels.telegram.*` beyond the name, `tools.mcp.servers[].url` / `.auth_secret`, all of
`[http]`, all of `[rabbitmq]`, `vault.path` / `vault.password_file` / entry names / entry
values, `memory.sqlite_url` and every other `*_url`, and `runtime.owner_id`.

`extra_headers` and `base_url` are the two that read as harmless and are not: the first is where
people put `Authorization`, the second can be an internal endpoint. `owner_id` is identity, and
nothing in the v1 skills needs it.

### Rule for growing the list

An addition qualifies when all four hold: it names **one** exact field; it is non-secret *by
construction*, not by inspection of today's value; it answers a question one of the shipped
skills actually asks; and the sentinel leak test still passes. Anything that fails one of these
stays out — the list is meant to grow one verifiable field at a time.

**Division of labour with the system prompt.** `build_environment_prompt_fragment`
(`app/environment_context.py:9`) stays the place for the two or three facts worth paying for on
every single turn (cwd, managed root, confined/yolo mode, and after PR 1 version + config path).
The broad enabled-map costs nothing until asked, so it lives behind this tool. `write_dir` stays
duplicated into `list_skills` (PR 2d) deliberately — the agent creating a skill is already
there, and one field saves a round trip; both read it off `SkillRegistry`, so there is one source.

**Two wiring decisions:**

- **Workers.** A tool wired only into `factory.py` does not exist for task workers. To make it
  visible, add it to `_build_worker_tools` (`adapters/tasks/worker.py:166`) **and**
  `_WORKER_TOOL_ALLOWLIST` (`worker.py:45`). Probably yes — workers ask the same questions.
- **Specialists.** A specialist with neither `tools_allow` nor `tools_deny` gets zero non-MCP
  tools, so `get_settings` must be listed explicitly in any agent that needs it.

Name is free of the alias table (`http_client`, `calculator`, `datetime_now`,
`artifact_insert` — `llm/services/tool_executor.py:29`), which is what uniqueness is keyed on.

**Tests — the one that matters:** build a `Settings` with every secret-bearing field set to a
unique sentinel, call the tool, assert the sentinel appears nowhere in the serialized result.
That is the test that keeps catching leaks as `schema.py` grows. Plus: a disabled section is
absent from the payload entirely.

**Docs:** `docs/tools.rst`.

**Done when** `get_settings` on a minimal config returns only the sections that are on, and the
sentinel test passes with every secret field populated.

---

## PR 5 — `minibot-docs`

**Impact:** the most user-visible of the remaining skills — "ask the bot how the bot works" —
and pure markdown, so the risk is in the wording, not the code.

Self-knowledge: the agent *is* MiniBot, and users ask it how MiniBot works. Without this it
invents config keys that do not exist.

Fetch targets, in order:

1. `https://sonic182.github.io/minibot/llms.txt` — 2.7 KB, a curated page index. Cheap enough
   to fetch on every lookup.
2. `https://sonic182.github.io/minibot/_sources/<page>.rst.txt` — Sphinx publishes the RST
   sources (`docs/_build/html/_sources/` confirms it; `html_copy_source` is on by default).
   Clean RST beats scraping rendered HTML with `http_request`.
3. `https://sonic182.github.io/minibot/llms-full.txt` — still only 6.7 KB, an expanded index
   rather than full content, so it is a fallback, not the primary source.

The rule that makes this skill correct rather than merely useful — **two different questions
hide under "ask about minibot"**:

| question | answer from |
|---|---|
| "How do I enable the vault?" | docs |
| "Am I running the vault?" | `get_settings` (PR 4) — never the docs |

The published docs describe the latest release, not this process. The skill must say so, and
must refuse to answer a configuration-*state* question from documentation.

**Done when** a docs question is answered from `_sources/*.rst.txt` and a "do I have X enabled"
question routes to `get_settings` instead.

---

## PR 6 — `install-skill` (was `import-skill`) ✅ implemented on `feat/import-skill`

**Impact:** the growth path — it is how a user gets skills without writing them. Sequenced late
because it is the only PR that pulls remote content into the agent's instruction set.

**As built — a native tool, not a bash-run script.** The bot may have no `[tools.bash]`, and
some installs want zero Node, so `install_skill` (`llm/tools/skill_installer.py`) is a Python
counterpart of `npx skills add`: stdlib `tarfile`/`zipfile`/`hashlib` plus the existing
`aiosonic`, no new dependency. Wired in `factory.py` next to `SkillLoaderTool` (it needs the
registry), gated by `[tools.skills] install = false` by default; while off, the bundled
`install-skill` skill is hidden too (`SkillsToolConfig.disabled_native_skills`).

- Sources: `owner/repo`, `owner/repo@skill` (npx semantics: `@` names a skill, not a ref),
  `owner/repo/path`, GitHub tree/blob URLs, any https `.zip`/`.tar.gz`/`SKILL.md` URL. No local
  paths. HTTPS only. GitHub fetches `codeload.github.com/{o}/{r}/tar.gz/{ref or HEAD}`.
- npx limits: 10 MiB download, 25 MiB / 1000 files extracted; tar `filter="data"`.
- Discovery: walk ≤ 5 levels, skip `.git`/`node_modules`/…, shallowest `SKILL.md` per name wins,
  each validated by `parse_skill_file` (now public, raises `ValueError` with the reason, which
  the preview reports).
- `install: false` previews (name, description, `compatibility`, files, invalid + reasons);
  `install: true` copies into `write_dir` (or a `dest` that is one of the discovery paths),
  `force` overwrites.
- `<dest>/skills-lock.json` in the npx local-lock shape (`version: 1`, `source`, `sourceType`,
  `ref`, `skillPath`, `computedHash` with the same hashing scheme).

**Riding along:** `enabled` frontmatter is gone (a skill is on when its folder exists);
`compatibility` is parsed and returned by `activate_skill`, whose description tells the model to
report a missing tool and stop rather than improvise. `write_path` now defaults to
`~/.minibot/skills`.

**Security stance, stated in the skill body itself:** never auto-activate; show name,
description, `compatibility` and the resolved URL; explicit confirmation for a source the user
did not name.

---

## PR 7 — `create-agent`

**Impact:** narrowest audience of the four skills — specialists are an advanced feature. With
`reload_agents` already in (PR 3) this is pure markdown, the same file-drop shape as the others.

Authoring `agents/<name>.md` is the same file-drop shape as a skill, but the tool-scoping rules
are genuinely counterintuitive and documented nowhere the agent can see:

- with **neither** `tools_allow` nor `tools_deny` set, a specialist gets **zero** non-MCP tools
  (`app/agent_policies.py:48`) — not "all tools", which is the intuitive reading
- `tools_allow` is **never consulted** for an MCP tool name; MCP tools are gated by
  `mcp_servers` membership plus deny patterns only
- `tools_allow` and `tools_deny` are mutually exclusive (enforced twice: `tool_policy_utils.py`
  and a pydantic validator in `schema.py`)
- these names are always stripped from a delegated agent (`agent_policies.py:15`):
  `fetch_agent_info`, `spawn_task`, `list_tasks`, `get_task`, `cancel_task`
- an empty Markdown body is **fatal**, not a warning (unlike skills, where it is a silent drop)

An agent writing a specialist without knowing this produces a broken specialist that looks
correct.

Body flow: read `agents.directory` from `get_settings` (PR 4) — or fall back to the configured
default if PR 4 has not landed — write `<directory>/<name>.md`, then call `reload_agents`
(PR 3). An `ok: false` result names the file and the problem; fix and reload again until it
comes back `ok: true` with the new name in `added`.

**Done when** a specialist written by following the skill is delegatable in the same session.

---

## Open questions

- `write_path` default: `./.agents/skills` (consistent with discovery) or `./skills` (what
  `config.example.toml:526` already recommends)? Blocks PR 2.
- Do native skills need to be visible to task workers? Workers build tools independently
  (`adapters/tasks/worker.py:166`) and load no bundled extensions. Affects PR 2 and PR 4.
