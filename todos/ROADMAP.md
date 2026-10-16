# Roadmap

MiniBot has moved from "personal assistant with tools" towards a **personal agent runtime**: a
supervisor that owns Telegram, memory, scheduling, approvals and MCP, and delegates work to
specialists, background tasks and, next, external agents.

```text
                         ┌─ specialist
                         ├─ background task
User → MiniBot → agent ──┼─ scheduled task
                         ├─ MCP server
                         └─ external agent (ACP)
```

What made this possible is the combination of `spawn_task`, `delegate` (a worker's result can
feed the main agent's next step instead of ending in a dead-end message), specialists with their own
tools and providers, runtime agent reload, the approval gate and `get_settings` / `minibot-docs`.
The work ahead therefore favours making delegation, interruption and cancellation dependable over
adding more capability.

## Status at a glance

Pending work, in priority order. Phase numbers are stable identifiers (other sections refer to them),
so the order here is the order to work in, not the numeric order.

| priority | phase | topic | status |
|---|---|---|---|
| 1 | 11 | task observability | pending |
| 2 | 10 | external agents (ACP) as an agent runtime backend | pending, experimental through `acpx` first |
| 3 | 3b | steering leftovers | steering workers and specialists, deferred until the addressee is decided |
| 4 | 4 | MCP OAuth (issue #65) | demand-driven: only when an MCP server in use requires it |
| 5 | 5, 6 | security defence in depth: guardrail heuristics, bash hardening | pending, lower priority than the deterministic controls already shipped |

Shipped and removed from this file: 0 bash env default, 1 credential vault, 2 skills for specialist
agents, 3 native skills, runtime self-knowledge and agent management, 3b main-agent steering and `/stop`
(Telegram, web, and `Esc` in the console TUI), 7 shared tool construction with task workers, 8 task lifecycle,
bounded output, MCP HTTP and approval binding (PR #112, released in 0.29.0) plus the container smoke test of task
cancellation, 12 opt-in bundled extensions for task workers (`[tasks] worker_extensions`), 9 delegation semantics
(`delegate` versus background `spawn_task`, `[tasks] delegation_mode`), 13 codemode (`run_tools_script`, Python that
calls tools in a Monty sandbox, `[tools.codemode]`, built over an agent's final tool list through
`ExtensionContext.add_tool_deriver`). The commit history keeps their design notes.

Decided: scheduler cron expressions stay in UTC, as documented in `docs/scheduler.rst`; there is no
`[scheduler]` timezone setting.

Dropped: a native SMTP tool. Mail is covered by an MCP server (`docs/mcp_servers.rst`) behind the
`[tools.approval]` gate, so there is nothing left for MiniBot to own.

Parked on purpose: more self-modification (agent auto-evolution, automatic skill or tool creation,
self-tuning prompts, autonomous agent spawning). `create_agent` / `update_agent` / `delete_agent` /
`reload_agents` are enough metaprogramming; the effort goes into delegate, interrupt, resume, cancel
and observe.

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
  (`bash.pass_parent_env = false`), secrets never reaching the LLM (the credential vault),
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

## Known gaps

Deliberately left, still true after the runtime reliability work (Phase 8, shipped):

- A `bash` command that detaches a process into its own session (`setsid`) escapes the process-group kill on
  cancel and timeout; only container or VM isolation bounds that.
- Scheduled-prompt retries only cover a failing event-bus publish, not a failing turn.
- Stdio MCP clients are bound to one event loop: `MCPClient._ensure_stdio_runtime` kills and respawns the
  server process whenever a different loop calls in (startup `asyncio.run` versus the daemon loop).
- `Retry-After` from the provider cannot be honoured: `llm_async` raises a bare `Exception("HTTP 429: ...")`
  without headers (`ProviderHTTPError` only carries status and body), so `_build_error_metadata`
  (`app/tasks/worker.py`) keeps a fixed 30 s backoff.
- A lazy MCP `call_tool` is recognised by tool name suffix in `app/tool_approval.py`; an eager remote tool
  literally named `call_tool` that also takes a `tool_name` argument would be misread. Fixing it needs an
  explicit marker carried through every `ToolBinding` wrapper.
- `bash` spill files hold at most `max_output_bytes`, since the cap is applied while reading.

## [ ] Phase 3b — Steering leftovers (priority 3)

Steering and `/stop` shipped for the main agent: the dispatcher hands same-chat messages to the running turn
(`app/turn_inbox.py`), and `/stop` exists on Telegram, in the web chat and as `Esc` in the console TUI. Still open:

- [ ] Steering a specific worker or specialist. Deferred on purpose: with several active tasks, "change this"
  needs an unambiguous addressee, so decide how the owner names the target before building anything.

Trust model: pending messages are owner input like any other, no new surface. `/stop` must remain
owner-only.

## [ ] Phase 10 — External agents through ACP (priority 2)

The goal is for MiniBot to be the control plane in front of other agents (Claude, Codex, OpenCode, Pi),
keeping Telegram, memory, scheduling, approvals and MCP as the stable interface. ACP is **not** another LLM
provider; it is another way to *run an agent*.

Model it as an agent execution backend, not as per-vendor tools. Do not add `ask_claude()`, `ask_codex()`
or `ask_pi()`; the model should only ever see agent names:

```text
Agent
├── prompt
├── tools
├── provider / model
└── runtime
     ├── minibot   (today, implicit)
     └── acp
```

```yaml
---
name: architect
runtime: acp
command: ["claude-agent-acp"]
model: opus
---
You are the architecture specialist.
```

`spawn_task(agent="architect", ...)` (or `delegate`) then runs it like any other specialist. The
pieces that already exist are the reason this fits: task id, timeouts, cancel, result, continuation, tool
approvals, agent definitions and provider overrides. ACP is one more way to execute a worker.

Staging, validating before absorbing:

1. Experimental backend through `acpx` (MiniBot → `acpx` → ACP → Claude). Claude Opus first, with
   persistent sessions, cancellation and a working directory.
2. Then Codex, OpenCode and Pi.
3. Only after weeks of use, when the needed subset of ACP is known, consider a native ACP client.

Depends on Phase 8 (cancel and process-tree cleanup must be dependable for a long-lived external process)
and fits `delegate`, which returns its result to the main agent. Trust model: an external agent has its own tools and filesystem access, so it is
subject to the same deployment-isolation reasoning as `bash` (see Phase 6) and should sit behind the
approval gate.

## [ ] Phase 11 — Task observability (priority 1)

As delegation grows (specialists, continuations, external agents), the owner needs to see what happened:

- Better `list_tasks` / `get_task`.
- Parent / child task relationships across continuations.
- Duration, tokens and cost per task.
- Which agent and model actually resolved each piece of work.

Small and incremental; builds on the task records that already exist, no new subsystem.

## [ ] Phase 4 — MCP OAuth (issue #65) — demand-driven (priority 4)

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
- Resulting tokens stored in the credential vault (`minibot/adapters/vault/`), keyed by
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
  conversation memory (SQLite), then compaction summaries, permanently. The
  vault deliberately shipped without it: the choke point is
  `execute_tool_calls_for_runtime` (`minibot/llm/services/tool_executor.py`),
  redacting any known secret value out of a `ToolResult` (and logs) before it
  reaches the LLM. The same echo risk already exists today for `${ENV_VAR}`
  static MCP headers. It is a literal containment check against known values,
  not semantic classification, so the output-classification rule allows it.
- MCP token refresh will need a lock once the OAuth client exists (there is none yet).
  `MCPClient` is per-server with no mutex
  around refresh — two tool calls near token expiry could both refresh
  concurrently; some providers invalidate the old refresh token when issuing
  a new one, so the loser of that race gets locked out. Needs a lock keyed by
  `(server_name, issuer)`.

## [ ] Phase 5 — Guardrail enhancements (priority 5, defence in depth)

Not a duplicate of the credential vault. Under its destination-bound model,
the LLM never has a secret reference to put in an argument at all, so nothing
checks argument *content* for known vault values; the planned redaction check
(Phase 4) only covers tool *results*. This phase covers the input side: a secret that entered the conversation another way entirely (the user
pastes a raw API key into chat instead of storing it, or the LLM produces
something secret-shaped) and could otherwise get echoed into a later tool
call's arguments:

- `ToolGuardrailValidator` gains a check for secret-*shaped* values in
  arguments — entropy/prefix heuristics (`sk-`, `ghp_`, long high-entropy
  tokens), independent of whether the value matches a known vault entry.
- `GuardrailDecision` gains a `credential_exposure` field (structured, not
  regex/text classification, per project convention).

## [ ] Phase 6 — bash tool hardening (mixed priority — see Trust model) (priority 5)

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

- `BashToolConfig.pass_parent_env` defaults to `False` with an `env_allowlist`,
  but an owner who sets it back to `true` (as `config.yolo.toml` does) still
  exposes every `${ENV_VAR}` config secret to `bash` → `env`.
- `bash`'s `cwd` (`_coerce_cwd` in `llm/tools/bash.py`) accepts any existing
  directory on the filesystem — there's no root jail at all, unlike
  `LocalFileStorage` (`adapters/files/local_storage.py`), which
  refuses to resolve a path outside its managed root by default.
- `python_exec` already has a `sandbox_mode` field (none/basic/rlimit/cgroup/
  jail) and a working `jail` implementation that just prepends a configurable
  `command_prefix` (e.g. `bwrap`, `firejail`, `nsjail`) to the command
  (`llm/tools/python_exec.py`, `PythonExecJailConfig.command_prefix`). `bash`
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
   empty `command_prefix` today (`config.example.toml`, comment
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

## Explicitly deferred

- MCP OAuth HTTP callback endpoint (issue #65 alternative 2).
- MCP OAuth device flow (issue #65 alternative 3).
- A native SMTP tool: sending mail goes through an MCP server, gated by `[tools.approval]`.
- A sandbox platform inside MiniBot (a Rust supervisor, per-call containers): isolation stays the
  deployment's job; MiniBot offers the jail wrapper and documentation (Phase 6).

Add any of these only if something actually in use requires it.
