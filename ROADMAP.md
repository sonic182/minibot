# Roadmap

Possible roadmap to follow now...

## Phase 0 — fix bash's env default now (no dependency on anything else)

`BashToolConfig.pass_parent_env` defaults to `True`
(`minibot/adapters/config/schema.py:572`), so `bash` inherits the daemon's
entire process environment today, vault or no vault — any `${ENV_VAR}`
secret already used for config (`GITHUB_TOKEN`, static MCP header tokens, DB
URLs) is retrievable right now via `bash` → `env`. Flip the default to
`False` with an explicit `env_allowlist`, matching `python_exec`'s existing
default (`schema.py:560-561`) — `bash` is the outlier, not the norm. Small,
immediate, and a prerequisite for Phase 1's `MINIBOT_VAULT_PASSWORD` unlock
option to be trustworthy at all.

## Phase 1 — Credential vault (reference-only)

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
  once at daemon startup, owner's choice of method — interactive prompt
  (blocks start, no password on disk), `--vault-password-file`, or
  `MINIBOT_VAULT_PASSWORD` — then kept only in the vault adapter's memory,
  never exported to a subprocess. **This is not safe under bash's current
  default** (see Phase 5 — `pass_parent_env=True` copies the whole process
  environment into every `bash` call). The env-var unlock method only holds
  once that's fixed. Whichever method is used, that password/file becomes the
  thing to protect instead.
- CLI helper `minibot vault edit <path>` — like `ansible-vault edit`:
  decrypt to a 0600 temp file, launch `$EDITOR`, re-encrypt on exit, shred the
  temp file. This is how the owner writes secrets; the LLM never gets a
  write path either.
- LLM-facing tool surface: `list_secrets()` (names only). No `get_secret`
  tool at all — resolution of `secret://name` happens inside `http_client`
  and `MCPClient` when building outbound headers, never via a callable tool.
- No new "hooks" framework needed: `execute_tool_calls_for_runtime`
  (`minibot/llm/services/tool_executor.py:279`) already funnels every tool
  call through one point before `binding.handler(...)` runs. That's where
  `secret://name` substitution happens, and where a raw-secret-value check on
  `arguments` rejects a call instead of running it. Both are literal
  string/containment checks against known vault entries, not semantic
  classification — allowed under the project's output-classification rule.

Out of scope for this vault: today's `${ENV_VAR}` config-time secrets
(`token_env`, static MCP headers). Different threat model — admin-authored,
live only in `config.toml`, never handled through tool arguments. No
migration planned; the two coexist.

Ceiling: this protects secrets at rest and from the LLM's own tool calls. It
does not protect against a fully compromised daemon process reading its own
memory (e.g. `/proc/<pid>/mem`) — same trust boundary as any self-hosted
secret manager running as one OS user. Out of scope unless that threat model
changes.

## Phase 2 — MCP OAuth (issue #65)

Scope: alternative 1 only (auth-code + PKCE + manual callback paste). No HTTP
callback endpoint, no device flow.

- `MCPClient` (`minibot/adapters/mcp/client.py`) catches `401` on HTTP
  transport, runs MCP OAuth discovery, holds PKCE state.
- Resulting tokens stored in the Phase 1 vault, keyed by
  `(server_name, issuer)`.
- `_build_http_headers` resolves the vault reference into the `Authorization`
  header at request time.
- Owner-only admin surface (not an LLM tool) to present the auth URL and
  accept the pasted callback, via Telegram authorization.

- Output side, not just input: nothing here stops a resolved secret coming
  *back* in a tool result (an API that echoes the `Authorization` header in
  an error message, an SMTP server's debug reply) and landing in
  `ToolResult.content` — which flows into LLM context, then conversation
  memory (SQLite), then compaction summaries, permanently. The same
  raw-value containment check planned for `arguments` needs to run
  symmetrically on the result before it's returned.
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

## Phase 3 — SMTP tool

- `SMTPToolConfig` next to `HTTPClientToolConfig`.
- Credentials passed as a `secret://` reference, resolved at send time.
- No new credential-handling code — reuses Phase 1 vault.

## Phase 4 — Guardrail enhancements

Not a duplicate of Phase 1's containment check. Phase 1 only catches a
value already *stored in the vault* appearing in arguments — it does
nothing if a secret got into the conversation another way (the user pastes
a raw API key into chat instead of storing it first, or the LLM produces
something secret-shaped). This phase covers that gap instead:

- `ToolGuardrailValidator` gains a check for secret-*shaped* values in
  arguments — entropy/prefix heuristics (`sk-`, `ghp_`, long high-entropy
  tokens), independent of whether the value matches a known vault entry.
- `GuardrailDecision` gains a `credential_exposure` field (structured, not
  regex/text classification, per project convention).

## Phase 5 — bash tool sandboxing (problem definition only, no decision yet)

Everything above assumes secrets are safe from `bash` as long as they never
appear as plaintext arguments or in a file it can read. That assumption
doesn't hold today:

- `BashToolConfig.pass_parent_env` defaults to `True`
  (`minibot/adapters/config/schema.py:572`), and `_build_env`
  (`minibot/llm/tools/bash.py:187-198`) does `env = dict(os.environ)` in that
  case — the LLM's `bash` tool inherits the daemon's *entire* process
  environment by default. Any `${ENV_VAR}` secret already used for config
  today (`GITHUB_TOKEN`, static MCP header tokens, DB URLs) is retrievable
  right now via `bash` → `env`, no vault involved.
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

Options to weigh later (not decided):

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
