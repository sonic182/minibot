# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.21.0] - 2026-09-22

### Added

- **Search and infinite scroll on the web pages.** `/history`, `/memory` and `/scheduled` each get a
  search box, and every list loads the next page as you scroll, with a plain "older" link still there
  without JavaScript. History search is full-text (SQLite FTS5) over message content, across all
  conversations on the index — with a match count per conversation — or within one conversation; session
  ids match too. Memory search reuses the key-value store's existing FTS5 ranking; scheduled prompts
  match their text ignoring the case of ASCII letters, and accented text matches as typed. History pages by cursor, so new messages arriving while you
  scroll cannot shift or repeat a page; memory and scheduled keep offsets, since relevance ranking and a
  recurring job's moving `run_at` have no stable key. A conversation now reads newest first.
- **`/chat` loads older messages as you scroll up.** The WebSocket sends the latest 50 messages instead
  of 200, and asks for the next 50 when you reach the top, keeping your scroll position. A reconnect
  replaces the conversation instead of appending a second copy of it.
- **Icons in the web menu.** Every navigation entry shows a Lucide icon. `mb.add_page()` takes
  `icon=` (a name from https://lucide.dev/icons, default `file`), so an extension's page picks its own.

### Changed

- **FTS5 searches quote each term.** Input such as `web:1` or `foo-bar` used to be parsed as FTS5 syntax
  — a column filter, an operator — and the error switched key-value memory search to `LIKE` for the rest
  of the process. Each term is now searched as text, and terms with no letter or digit (a lone `-`) are
  dropped instead of making an AND search match nothing.
- **`MemoryBackend.list_sessions()` is paged.** It takes `query`, `cursor` and `limit` and returns a
  `SessionPage`; `get_history_page()` is new. A custom backend needs both. `ExtensionContext.pages`
  entries are `(path, label, icon)` triples.
- **Web paging parameters are validated.** A non-integer, negative or out-of-range `offset` on
  `/memory` and `/scheduled` answers 400; a negative one used to be treated as 0 and a huge one as a
  server error. Edits, deletes and cancels there return to the first page.
- **Static assets are served gzip-compressed** when the browser accepts it, so the vendored Lucide and
  Alpine bundles download at about a fifth of their size (655 KB → 120 KB, 103 KB → 24 KB). Pages are
  left uncompressed: they carry CSRF and socket tokens next to reflected input like the search query,
  the combination a BREACH-style attack needs.

### Fixed

- **The desktop menu no longer slides in after the page draws.** The sidebar started hidden and the
  script opened it after first paint, shifting every page to the right. The CSS now sets the default per
  screen size, and the page stays hidden until its icons are drawn, instead of popping them in.
  Crossing that size — a rotated tablet, a resized window — returns the menu to its default there
  instead of leaving it inverted.
- **`/chat` no longer flashes hidden controls while loading.** "Thinking…", the recording notice and the
  upload controls showed until Alpine started.
- **The delete and cancel confirmations on `/memory` and `/scheduled` open again.** Their inline script
  was blocked by the page's Content Security Policy, so the buttons did nothing.

## [0.20.0] - 2026-09-22

### Added

- **Delegation can pick its provider, model and reasoning effort at call time.** `spawn_task` accepts
  optional `model_provider`, `model` and `reasoning_effort`, so the main agent can run a specialist on
  another configured provider for one task — a `chatgpt_codex` orchestrator delegating to a subagent on
  an OpenCode Go model, for example — without editing `agents/*.md` or restarting. `fetch_agent_info`
  now also returns the agent's own defaults and the providers that have credentials configured; anything
  else is refused with `provider_not_available` instead of falling through to the echo fallback. A
  retargeted task re-derives its context window and output cap from the target model, so mid-run
  compaction stays armed. The target is resolved the way `create_for_agent` resolves it — override,
  then the agent's own value, then `[llm]` — so an agent whose frontmatter names no provider, and a
  task with no `agent_name` at all, resolve against the model they actually run on instead of
  against `None`. Both numbers travel in the task payload, because the worker is a cold subprocess
  whose limits cache would need a full catalog download to work them out.
- **`chatgpt_codex` is only advertised with loadable OAuth credentials.** `available_providers()`
  treated any section with that `api_format` as usable, so an empty `[providers.chatgpt_codex]`
  passed the credential gate and queued work that failed only once the worker built the client. The
  configured (or default) auth path is now read through the existing credential loader, and a
  missing, unreadable or uninstallable one drops the provider from the roster like any other.
- **Named provider sections.** `[providers.<name>]` accepts `api_format` (`openai`, `openai_responses`,
  `openrouter`, `claude`, `google`, `chatgpt_codex`), so a section name can be anything and several
  endpoints of the same API can coexist — `[providers.opencode_go]` next to `[providers.zai]`. A section
  whose name is not itself an API format must declare one, instead of silently resolving to the OpenAI
  Chat Completions client. The new `models` list is advisory: it is what the main agent is offered to
  choose from.
- **`minibot configure` sets up several providers at once.** The provider step is a multiselect: each
  chosen target is written as its own `[providers.<name>]` section with `api_format`, key, base URL and a
  `models` roster picked from the endpoint's own `/models` list, and a final question chooses which one
  the main agent runs on — so delegation to another provider is configurable rather than hand-written. A
  target an older wizard had parked in a format-named section (z.ai inside `[providers.openai]`) is moved
  into its own section. Model lists longer than 25 entries get a search prompt with completion before the
  picker opens.

### Removed

- **`invoke_agent` is gone; `spawn_task` is the only way to delegate.** The two tools had grown into two
  full implementations of the same thing — tool scoping, initial state, prompt cache key, compactor,
  attachment handling — and `spawn_task` was already the superset: it takes per-call `timeout_seconds`,
  `max_steps` and `max_tool_calls`, reports progress, persists its result for `get_task`/`list_tasks`,
  can be cancelled, retries a rate-limited provider, runs in its own process, and can run a general
  worker with no `agent_name` at all. The one thing it does not do is return the answer inside the turn.

  **This changes what delegation means.** `spawn_task` acknowledges with a `task_id` and ends the turn;
  the specialist's answer arrives as a later message on the same conversation. The main agent can no
  longer fold a specialist's reply into its own response. `[tasks].enabled` therefore now defaults to
  `true` — the SQLite backend needs no broker and no extra — because with it off there is no delegation
  at all, and the specialist roster is dropped from the system prompt along with it.

  An agent's `timeout_seconds` keeps working: it is the default when a `spawn_task` call names none,
  capped by `[tasks].worker_timeout_seconds`. Two `[orchestration]` keys are retired with the tool that
  read them, `default_timeout_seconds` and `delegated_tool_call_policy` (and with the latter, the rule
  that a delegated agent must call at least one tool). A config that still declares them is accepted and
  ignores them. Response metadata loses `agent_trace` and `delegation_fallback_used`, which only ever
  described an in-turn delegation.

### Fixed

- **Specialist agents could not see any extension tool.** `build_enabled_tools` built the delegation tool
  before appending the extension tools, and the delegate copies the list it is handed, so every specialist
  was scoped against core tools alone — no `bash`, `filesystem`, `current_datetime`, `http_request`,
  `python_execute`, memory, graph, rag or scheduler — whatever its `tools_allow`/`tools_deny` said. An
  agent that needs `bash`, such as a playwright-cli specialist, could not work at all.
- **Reasoning replay no longer breaks strict providers.** The assistant's thinking text was echoed back
  on the next request as `reasoning`, which Fireworks rejects with HTTP 400 ("Extra inputs are not
  permitted"), killing a tool loop on its first step. The text now goes back under the key it arrived
  with — `reasoning_content` for DeepSeek and Kimi thinking-mode loops — and bare `reasoning` only for
  OpenRouter, the one target that reads it. `reasoning_details` is replayed as before, since the
  Responses API rebuilds its `rs_` item from it.

## [0.19.0] - 2026-09-21

### Added

- **The browser chat.** The optional HTTP server now serves an authenticated `/chat` page backed by a
  token-protected same-origin WebSocket channel. It shares the `web:1` conversation session with the
  other channels, so history, tools and model changes stay consistent, and it renders assistant Markdown
  safely. The page builds on vendored Alpine.js and Lucide assets rather than a CDN. The WebSocket
  authenticates its per-boot token through `Sec-WebSocket-Protocol`, not a URL query parameter.
- **Image and audio attachments in the browser chat.** A session can upload images — validated by magic
  bytes — and audio — validated by duration with `ffprobe` — which are stored temporarily under
  `uploads/temp/web`. The composer offers a file picker, drag-and-drop, paste and microphone recording,
  with previews before sending. Uploads require `[tools.file_storage] enabled`; audio also requires
  automatic transcription. `chat_upload_*` bounds attachment count, per-file and total size, and
  completed uploads are cleaned up after `chat_upload_retention_hours`.
- **Live tool activity in the web chat.** The authenticated `/chat` UI lists each tool invocation of the
  running turn and updates it from `started` to `completed`/`failed`, driven by `ToolCallEvent`. The tool
  name is forwarded; the redacted `detail` and `error` stay server-side. `ToolCallEvent` gains a `call_id`
  that pairs a call's lifecycle phases.
- `minibot configure` asks whether to enable `[tools.skills] install` when skills are selected, and
  whether to run the HTTP server (dashboard and browser chat), asking for a bind host, port and — when
  binding beyond loopback — a bearer token or basic credentials.

### Changed

- **`/static` caching follows the environment.** Assets are served with `Cache-Control: no-store` when
  `[runtime].environment` is `development` or `debug`, and with normal caching otherwise. The environment
  choices are defined once and shared by the configurator and the HTTP server so the two cannot drift.
- Bumped the indirect `anyio` dependency from 4.13.0 to 4.14.2.

### Fixed

- The documentation site now emits `og:description`, serves a favicon, and gives the home page a
  descriptive title and an install call to action.

## [0.18.0] - 2026-09-18

### Added

- **`install_skill` — a Python `npx skills add`.** With `[tools.skills] install = true`, the agent can preview and install published skills from `owner/repo`, `owner/repo@skill`, GitHub tree/blob URLs, or `.zip`/`.tar.gz`/`SKILL.md` URLs, without Node.js or `[tools.bash]`. Each candidate is validated with the runtime's own parser, installed into `write_path`, and recorded in a `skills-lock.json` in the npm `skills` format. The bundled `install-skill` skill has the agent show a preview and ask for confirmation before installing from a source the user did not name. The preview carries the skill's `hash` and the start of its instructions, and an install must pass that `hash` back as `expected_hash`, so a source that changed after the preview is refused. A skill that would override one of the same name from another location is reported as `existing` and needs `force`. Off by default, and the bundled skill stays hidden while it is off.
- **Skills can declare `compatibility`.** `activate_skill` returns it, and its description tells the model to report a missing tool and stop rather than imitate it with another one.

### Changed

- **`[tools.skills] write_path` defaults to `~/.minibot/skills`** (was `./.minibot/skills`), so created and installed skills follow the single user rather than the working directory. Docker Compose already mounts `~/.minibot`.
- **The skill `enabled` frontmatter field is gone.** A skill is on while its directory is in a discovery path; bundled skills are switched off with `native_disabled`. An existing `enabled:` line is ignored, so `enabled: false` skills now load: delete or move them to turn them off.
- Invalid skills are logged once as `invalid skill, skipping` with the reason. A skill description above 300 characters is logged at info instead of warning.
- **The frontmatter parser reads block scalars and skips list items.** `description: |` or `>` used to parse as the literal `|` or `>`, and an unindented list such as `allowed-tools:` followed by `- Bash` made the whole skill invalid. Both now parse, which matters for skills installed from other authors.

## [0.17.0] - 2026-09-18

### Added

- **Delegated runs compact their own context.** `spawn_task` and `invoke_agent` can loop for dozens of tool-calling steps inside a single `AgentRuntime.run()`, and nothing was watching the context window along the way — one research task finished at 1,035k tokens against a 1,050k limit. The runtime now measures the provider's reported input tokens each step and, past `memory.context_ratio_before_compact` of the model's window, compacts the working transcript down to the system prompt, the original task, and a summary, then keeps going. It reuses the strategy `HistoryCompactionService` already applies between turns: the provider's native compaction endpoint when available, an LLM summary otherwise, and on failure it simply carries on uncompacted. The main chat turn is unaffected — it still compacts its persisted history between turns instead.

- **MiniBot now ships its own skills.** A fresh install discovered skills only from disk, so `[tools.skills]` looked empty until someone hand-authored one. Skills bundled inside the package now load from a third discovery tier, ranked below project- and user-level so writing a skill of the same name overrides a bundled one rather than colliding with it. The tier is deliberately independent of `[tools.skills] paths` — that setting *replaces* the discovery list, and a bundled tier living inside it would vanish the moment anyone configured `paths`. `native = false` turns the whole tier off; `native_disabled = ["name"]` drops individual skills. The first bundled skill is `create-skill`, which teaches the agent to author one: to build it from the conversation that just happened rather than from general knowledge, where skills go, how MiniBot's frontmatter parser differs from the agentskills.io spec, and why an empty body silently drops a skill.

- **The agent can find out where it may write a skill.** `list_skills` now returns `write_dir` (the new `[tools.skills] write_path`, added as the highest-priority discovery path so a skill written at runtime is found without a restart), `discovery_paths`, a `source` of `project`/`user`/`native` per skill, and `write_dir_access` — `filesystem` or `bash`, since the `filesystem` tool is confined to `[tools.file_storage] root_dir` and refuses paths outside it. Computing that once beats letting the model discover it by failing a tool call.

- **`.minibot/skills` is a recognized skill directory**, at both project and user level, alongside the existing `.agents/skills` and `.claude/skills`.

- **The agent knows which version it is running and which config file it loaded.** Both are now in the environment context alongside the working directory, using the path actually loaded — so `minibot console --config other.toml` reports that file, not the default. `minibot --version` prints the version from the CLI, which had no version flag before.

### Changed

- **`.claude/skills` is no longer scanned by default.** Skill discovery now looks in `.minibot/skills` and `.agents/skills` (project, then user) rather than borrowing another tool's directory, and `.minibot/skills` is new. If you keep skills in `~/.claude/skills`, add it to `[tools.skills] paths` — which also disables the other defaults, so list every directory you want. In a checkout where `.claude/skills` symlinks to `.agents/skills`, this also stops discovery walking the same tree twice and logging a collision for every skill.

- **A tool call that cannot succeed no longer kills the whole run.** Two identical tool failures used to abort immediately with `repeated_tool_failure`, throwing away everything produced so far — a background task doing RDAP lookups died 35 seconds in because it retried one hostname that does not resolve. An unreachable host is a finding to report, not a reason to lose the work: the runtime now tells the agent once that the call cannot succeed and to surface it in its final answer, then lets it carry on. `max_steps`, `max_tool_calls` and the timeout remain the ceilings, and the separate guard for genuinely stuck loops (identical calls producing identical outputs) still stops those.

### Fixed

- **`minibot.__version__` reported `0.1.0` no matter which release was installed.** Hardcoded and referenced nowhere else, it had drifted fifteen minor versions behind `pyproject.toml`. It now reads installed distribution metadata, falling back to `0.0.0`.

- Agent specs no longer lose `omit_temperature` (and now `context_limit`) when a task runs them through `spawn_task`; the field-by-field copy became a `dataclasses.replace`.

- **One failed Telegram send left the bot permanently mute.** `_publish_outgoing` consumed its subscription with an unguarded `async for`, and only `TelegramBadRequest` was caught around the actual send calls. Any other failure — a rate limit, a network blip mid-way through a chunked message — escaped and ended the loop, killing the task for the rest of the process's life: the daemon kept receiving messages and generating answers, and silently delivered none of them. It was invisible too, since the task is held on an attribute and asyncio never reports an unretrieved exception for it. Worse, that subscription is bounded and non-lossy, so once 128 events piled up in the orphaned queue every `EventBus.publish` would block forever and hang the daemon outright. Each event is now handled in isolation, the send paths catch everything, a dead loop is logged at ERROR, and the bus warns before blocking on a full queue. The console channel's identical loop got the same treatment.

- **Mid-run compaction resent the whole transcript it had just shrunk.** For providers chained through `previous_response_id`, the step right after a native compaction fell back to rendering the full local state (system prompt, task, summary) instead of the delta-only follow-up every other step in that mode sends — duplicating content the newly compacted response already held server-side, right when compaction exists specifically to cut tokens. It now sends the same minimal continuation nudge `build_continue_call_kwargs` already uses to resume a `previous_response_id` without replaying history.

- **`spawn_task` results rendered as raw Markdown on Telegram** (literal `**bold**`, `# headings`, `|table|` pipes instead of formatted text). The worker already resolved the right `kind` (markdown/html/text) through the same `extract_answer()` path a normal turn uses, but only kept the resolved text and dropped the kind; the task manager then built the outbound response with no `render`, which falls back to forced plain text. The worker now carries `render_kind`/`render_meta` through its result metadata, and the manager reattaches them.
## [0.16.0] - 2026-09-16

### Added

- **Optional built-in HTTP server** with an authenticated dashboard, conversation history, and memory/graph review pages.
- **Scheduled prompt and MCP server pages** in the built-in HTTP UI.
- **Extension system-prompt fragments**, including MCP server instructions when its tools are attached.

- **`${secret:NAME}` config references.** Any string value in `config.toml` can now read from the
  encrypted vault, so provider API keys, the Telegram bot token and anything else no longer have to
  sit in plain text next to a `bash` tool that can `cat` the file. Resolution happens in a second
  pass after `${ENV_VAR}` and after the vault is unlocked; `$${secret:NAME}` escapes a literal;
  `[vault]`'s own settings cannot use it; a reference without `[vault] enabled = true` fails at
  startup. Task workers receive the vault contents over the in-memory pipe so references resolve
  there too. `minibot configure` preserves references rather than resolving them. The typed
  `[[tools.mcp.servers]] auth_secret` field remains supported as the `Authorization: Bearer`
  shorthand; setting both on one server is now an error.

- **Encrypted credential vault** (`[vault]`, opt-in, needs the new `vault` extra). Secrets live in
  a single AES-256-GCM file whose key is derived from a password with scrypt and never touches
  disk or a subprocess environment. `minibot vault init|edit|list` manages it, ansible-vault
  style: `edit` decrypts into `$EDITOR` via a `0600` temp file and re-encrypts on exit.
  Secrets are destination-bound — `[[tools.mcp.servers]] auth_secret` names a vault entry that the
  MCP client sends as its own `Authorization` header. The LLM gets one new tool, `list_secrets`,
  which returns names only; there is no `get_secret` and no `secret://` reference it can write
  into a tool argument. The password is read from `[vault] password_file`, then
  `MINIBOT_VAULT_PASSWORD`, then an interactive prompt — the prompt being the recommended method.
  See `docs/security.rst` for the threat model and its limits.

### Fixed

- MCP tool schemas with `$ref` siblings no longer fail request validation.
- Memory and graph review pages now validate mutations and cap graph listings.
- Basic Auth rejects cross-site state-changing requests, and MCP errors redact resolved credentials.

- **Console TUI crashed when a link in the transcript was clicked.** `MarkdownViewer` resolves
  every href as a local file path, so an `https://` link from the assistant raised
  `FileNotFoundError: .../https:/example.com/...` and tore down the app. External links now open
  in the browser and everything else is ignored — the transcript is a chat log, not a document
  browser, so navigating away was never wanted.

### Changed

- **`tools.bash.pass_parent_env` now defaults to `false`**, matching `tools.python_exec`. The `bash`
  tool previously inherited the daemon's entire process environment, so any `${ENV_VAR}` secret used
  in `config.toml` (`GITHUB_TOKEN`, MCP header tokens, database URLs) was retrievable by the LLM with
  a single `env` call. Only keys listed in `env_allowlist` (`PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`,
  `SHELL` by default) are forwarded now. Configs that omit the key will see commands lose variables
  they used to inherit — add the ones you need to `env_allowlist`, or set `pass_parent_env = true`
  explicitly to keep the old behavior.

## [0.15.0] - 2026-09-13

### Added

- **Configuration environment expansion.** Any string value in `config.toml` can now reference
  process environment variables with `${NAME}`, including nested tables and lists. Missing variables
  fail configuration loading with their path, `$${NAME}` preserves a literal reference, and
  `minibot configure` retains existing references while using resolved credentials for model discovery.

## [0.14.3] - 2026-09-13

### Fixed

- `chatgpt_codex` requests with an attached image (or any multi-part message) no longer fail with an
  HTTP 400 from the Responses API. The provider was missing from the media-input mode mapping, so
  text parts serialized as `text` instead of `input_text` once an image part was present.

## [0.14.2] - 2026-09-13

### Changed

- `llm-async` upgraded to 0.5.3, which carries the Responses API reasoning replay upstream.

### Fixed

- Responses API full-history tool follow-ups now replay the required reasoning items before function calls.

## [0.14.1] - 2026-09-13

### Changed

- Package metadata now identifies MiniBot as beta software and supports Python 3.14.

## [0.14.0] - 2026-09-13

### Added

- **ChatGPT Codex subscription provider** (#67). Use a ChatGPT Codex subscription as the LLM backend
  without an API key: authenticate with `minibot codex login` (including a device-code flow for headless
  hosts), select it from `minibot configure`, and configure `provider = "chatgpt_codex"`. Model
  capabilities are fetched from Codex, and Docker preserves OAuth credentials across restarts.
- **Richer Telegram conversations** (#66). MiniBot now supplies reply/quoted-message context to the model,
  replies in Telegram threads, sends a typing indicator while a turn is active, and uses Telegram rich
  messages when possible with a plain-text fallback.
- **OpenRouter app attribution.** OpenRouter requests now identify MiniBot by default; set
  `[llm.openrouter].attribution_enabled = false` to disable those headers.

### Fixed

- Responses API tool loops in full-history mode no longer drop tool outputs, and stateless providers such
  as ChatGPT Codex no longer rely on `previous_response_id` continuity.

## [0.13.0] - 2026-09-12

### Added

- **Interactive console TUI.** `minibot console` now opens a Textual app: a markdown transcript with a
  multiline prompt pinned to the bottom. Enter sends, `Ctrl+J` inserts a newline, `Ctrl+T` toggles
  model thinking, `Ctrl+C`/`Ctrl+Q` quit. The previous prompt loop is still available behind
  `--plain`, and `--once` is unchanged. Recent history for the console session is loaded into the
  transcript at startup, and turns go through the same dispatcher pipeline as before.
- **Reasoning capture for Responses providers.** `[llm].reasoning_summary` (`"auto"`/`"concise"`/
  `"detailed"`) asks the model for a plaintext reasoning summary alongside `reasoning_effort`, and
  the reasoning returned on a raw Responses payload is extracted per assistant message and exposed
  to channels as response metadata (`reasoning`). This is the only way to see thinking from
  providers that return reasoning as an encrypted item plus summary, and it is what the console TUI
  renders behind `Ctrl+T`.
- Reasoning is persisted with the message it belongs to: a nullable `messages.reasoning` column, a
  `MemoryEntry.reasoning` field, and an optional `reasoning=` argument on
  `MemoryBackend.append_history`. Existing SQLite databases are migrated in place on startup
  (`ALTER TABLE messages ADD COLUMN reasoning TEXT` when the column is missing).
- Durable background tasks now use execution leases with token fencing: expired SQLite and redelivered
  RabbitMQ tasks are recovered at least once, stale workers cannot overwrite newer attempts, and terminal
  task records are purged by a dedicated retention service.
- **Durable task results and retrieval** (#60). Terminal tasks persist their result — reply text,
  attachments, metadata and a typed `stop_reason` — alongside step-level progress and a compact
  `task_events` history, and the new `get_task` tool reads one record (with optional events) by
  `task_id`. `list_tasks` gained `status`/`limit` filters and now reads persisted records instead of
  only live process state, task statuses gained `running`, `retrying`, `cancelled` and `timed_out`,
  and cancelling a persisted row that is no longer active marks it cancelled through the store.
- **Per-task execution limits.** `spawn_task` accepts `timeout_seconds`, `max_steps` and
  `max_tool_calls`, each bounded by the new `[tasks].worker_max_steps` / `worker_max_tool_calls`
  ceilings (a positive integer or `"unlimited"`, the default). Hitting a limit stops the worker with a
  typed `stop_reason` (`max_steps`, `max_tool_calls`, `timeout`, etc.) instead of an opaque failure.
- **Reasoning capture beyond Responses providers, and live thinking events.** Reasoning returned
  beside `content` under `reasoning` or `reasoning_content` is now collected and persisted, and a flat
  `reasoning_effort` is sent to plain `/chat/completions` targets. A new `ReasoningEvent` is emitted
  as soon as one provider step returns reasoning, before the turn finishes, so a channel can render
  thinking while it is still running; the console TUI renders it live along with a redacted one-line
  notice per tool call.

### Changed

- **Task defaults raised.** `[tasks].worker_timeout_seconds` 60 → 1800,
  `[tasks.sqlite].lease_timeout_seconds` 300 → 2100 (now sized to the worker timeout plus a supervisor
  grace), and `done_retention_seconds` 86400 → 2592000 (30 days) for terminal rows and their compact
  event history. Existing SQLite task databases are migrated in place on startup, and
  `config.example.toml` / `config.yolo.toml` are aligned (`config.yolo.toml` also sets
  `reasoning_summary = "detailed"`).
- `ToolCallEvent` carries a pre-redacted `detail` string instead of `argument_keys`, built by
  `minibot/shared/tool_call_display.py` before the event is published so raw argument values (which can
  hold credentials) never cross the event-bus boundary. Extension subscribers reading `argument_keys`
  must switch to `detail`.
- Task worker rate-limit retries are now detected from a typed `ProviderHTTPError` with status 429
  instead of matching on error text, and use a fixed 30s delay.
- The audio-transcription model is loaded lazily on first transcription, in a worker thread, instead
  of at tool construction.
- `textual` is now the direct console dependency; `rich` (still used by the plain console and pulled
  in transitively by `textual`) is no longer declared directly.
- `LLMClient.provider_capability_hints()` returns a tuple instead of a list.

### Fixed

- **`bash` and `python_execute` hung forever when a command left a child holding the output pipes**
  (#62). `communicate()` waits for pipe EOF, not just process exit, so a backgrounded child that
  inherited stdout/stderr kept the call pending after the shell itself exited — a task blocked this
  way for 569s until the runtime timeout, orphaning the shell, an http server and a
  Playwright/Chromium tree. Two defects made it unrecoverable: `_terminate_process` skipped
  `killpg` whenever the direct child already had a returncode (the process holding the pipes was in
  that same group), and the drain that followed had no timeout. Both tools now share
  `minibot.shared.subprocess_utils`, which always signals the group, bounds the post-kill drain, and
  also kills the group on cancellation so a cancelled turn or task no longer leaks processes. The
  `bash` description gained the `setsid cmd >log 2>&1 </dev/null &` pattern for long-lived
  processes, since redirecting inside a backgrounded compound command leaves the subshell holding
  the descriptors.
- A turn that handed its work to a background task is no longer replayed as a pending turn after a
  restart: `pending_turns` gains a `task_handoff_completed` flag, set when `spawn_task` enqueues, and
  `list_pending` skips those rows. Existing tables are migrated in place.

## [0.12.0] - 2026-09-12

### Added

- **RAG now has a pluggable vector backend, and SQLite is the default.** `[tools.rag].backend`
  selects `"sqlite"` (new) or `"qdrant"`, mirroring how `[tasks].backend` picks between SQLite and
  RabbitMQ. The SQLite backend keeps vectors in a local file (`sqlite_url`, default
  `./data/rag.db`), so enabling RAG no longer requires running a vector database. Scope filters
  (`user_id`/`agent_id`/`chat_id`/`document_id`/`filename`) run in SQL before scoring and the
  similarity scan is exact, which suits filtered single-tenant corpora; Qdrant remains the option
  for corpora large enough to need an approximate index. Both live behind the new
  `core/vectors.py::VectorStore` protocol. `minibot configure` asks which backend to use when RAG is
  enabled, then prompts only for that backend's location (`sqlite_url` or `qdrant_url`).
- Startup warns when `tools.rag.backend` resolves to the `"sqlite"` default while `qdrant_url` is
  customized — the shape of a config written before `backend` existed, which would otherwise start
  against an empty local store without failing.
- **Relation graph tool** (#54), opt-in through `[extensions] modules` plus the new `graph` extra.
  Facts that name two things are stored as edges in SQLite and traversed with `networkx`, so
  "my sister lives in Madrid" becomes `person:sister --lives_in--> city:madrid` instead of another
  memory blob. A `policies/graph.md` prompt fragment tells the model when to reach for it over
  `memory`, and `docs/graph.rst` covers setup. Registered from `[extensions] modules` rather than
  the bundled list specifically so it also reaches task workers.
- **Lazy MCP tool discovery** (#56). `[tools.mcp].mode = "lazy"` stops the daemon from fetching
  every configured server's catalog at startup, fetching on first use instead and caching for
  `catalog_cache_ttl_seconds` (default 60). `mode = "bridge"` keeps the previous eager behavior.
- `minibot-dev` agent skill (#57): an orientation and change-routing guide for working on MiniBot
  itself — layer map, per-extension-point wiring chains, the two system-prompt mechanisms, and the
  project invariants that are not visible in the file tree. `CONTRIBUTING.md` added alongside it.

### Changed

- Tool descriptions now live beside the module that builds the tool (`minibot/llm/tools/graph.txt`
  next to `graph.py`) instead of in a central `minibot/llm/tools/descriptions/` package, and
  `load_tool_description` takes a `package` argument so a tool moved out of core keeps its
  description. Internal refactor; the description text and every tool are unchanged.
- **Breaking:** `[tools.rag].backend` defaults to `"sqlite"`. An existing deployment using Qdrant
  must add `backend = "qdrant"` to `[tools.rag]` in `config.toml`; otherwise RAG starts against an
  empty local store. There is no automatic migration — documents are reindexed from the managed
  file workspace. The `minibot-qdrant` service in `docker-compose.yml` is now commented out, like
  `minibot-rabbitmq`; uncomment it when using that backend.
- `numpy` joins the `rag` extra (the SQLite backend's similarity scan). It was already installed in
  practice as a sentence-transformers dependency, but was undeclared.
- **Breaking: one owner, many chat sessions** (#58). `[runtime].owner_id` replaces
  `[tools.kv_memory].default_owner_id`, which despite its location owned the relation graph,
  scheduled jobs and the RAG corpus as well as key/value memory, and applied even when that tool
  was disabled. Move the value; the name is all that changes. The old key is **rejected at load
  time** with a message naming the new one, rather than ignored — silently falling back to the
  default would have orphaned a customized owner's data.
  MiniBot assists exactly one person, so ownership is now a constant of the deployment: it is read
  once from config and never derived from a message, a task payload or any caller-supplied field,
  on any entrypoint. Sessions are chat sessions (`channel` + `chat_id`); a channel's `user_id` is
  authorization and audit context only and is no longer part of the session key. Session IDs are
  stored directly as `channel:chat_id`; histories created with earlier hashed identifiers are not
  migrated. Multi-user or multi-tenant
  isolation stays out of scope — it would need its own identity model in an opt-in extension.
- Dependency bumps across the pip group (#55).
- `aiogram` is updated from 3.27.0 to 3.31.0 and optional `aiohttp` from 3.13.5 to 3.14.3.

### Fixed

- **A `spawn_task` worker wrote long-term data under the wrong owner** (#58). It derived the owner
  from the task's `user_id` and never read config at all, so a worker used the channel's sender id
  while the main agent used `"primary"`. The relation graph is reachable from workers by design and
  every graph query filters on `owner_id`, so edges a task recorded were invisible to the main
  agent — silently, because the owner check was satisfied by either value. Key/value memory, RAG
  and the scheduler escaped it only because they do not load in worker processes. Anyone who ran
  the graph tool together with `spawn_task` can reclaim those rows with
  `UPDATE graph_edges SET owner_id = '<owner_id>' WHERE owner_id = '<sender id>'`.

## [0.11.0] - 2026-09-10

### Changed

- **Optional dependencies are now actually optional.** `aiogram` + `telegramify-markdown` and
  `pypdf` moved out of the core dependency set into the new `telegram` and `rag` extras; the bundled
  extensions that need them now import them lazily, after their config gate. A daemon that never
  enables the Telegram channel no longer needs aiogram installed, and RAG works without pypdf (PDF
  ingestion reports the missing extra). `pip install "minibot[telegram,rag]"` restores the previous
  install surface. `selectolax` (compact HTML rendering in `http_request`) stays a core dependency,
  but is now imported lazily on first HTML compaction so a disabled HTTP tool never loads it.
- **Breaking:** the `mcp` extra is gone: nothing at runtime imports the `mcp` package — the MCP
  client is a self-contained JSON-RPC implementation. Only the test fixtures use it, so `mcp` is
  now a dev dependency. `pip install "minibot[mcp]"` still works (pip warns about the unknown
  extra), but `poetry install --extras mcp` now fails.
- Two internal behavior changes ride along: `html_compact` skips tagless nodes instead of raising
  `TypeError`, and the RAG reranker wraps non-numeric model scores in a `RuntimeError` instead of
  leaking `TypeError`/`ValueError`.

## [0.10.0] - 2026-09-10

### Added

- **Python extension system.** `[extensions] modules` names importable modules, each exposing a
  `register(mb)` that contributes LLM tools, subscribes to internal events, and registers
  long-running services. Resolved by normal Python import, so pip-installed packages and local
  one-file modules on `PYTHONPATH` both work. `[extensions.config.<module>]` carries a free-form
  settings slice per module, reaching the extension as `mb.config`; `Settings` keeps
  `extra="forbid"` everywhere else. See `examples/minibot_ext_demo.py` and `examples/README.md`.
- Extension load failures are fatal by design: a module that cannot be imported, defines no
  `register`, or whose `register` raises stops startup naming the module. A handler that raises at
  runtime is logged and its subscription survives.
- **Turn and tool lifecycle events**, useful on their own for observability: `TurnStartedEvent`,
  `TurnCompletedEvent`, `TurnFailedEvent`, and a single `ToolCallEvent` carrying
  `phase = "started" | "completed" | "failed"`. Tool events are emitted by a wrapper
  (`minibot/llm/tools/tool_events.py`) applied in `build_enabled_tools`, so the three tool-execution
  call sites are untouched and extension tools get emission for free. Payloads carry argument *keys*
  only, never values or results. `ToolContext.turn_id` correlates a tool call to its turn.
- `EventBus.subscribe(types=...)` filters at subscription time, so an event never occupies a slot in
  a queue that does not want it; `None` still means receive-everything. `lossy=True` drops with a
  warning on a full queue instead of applying back-pressure — core subscribers stay blocking, only
  extensions are lossy, so a slow third-party handler can no longer stall the bus.
- **Bundled extensions are grouped by role** under `minibot/extensions/{channels,integrations,
  services,tools}`. Telegram, RAG, MCP, RabbitMQ, scheduler, SQLite tasks, and optional tool groups
  now use the same `register(mb)` API as third parties; their adapter and LLM-tool implementations
  remain in their existing layers.
- Task workers now load extension tools with `entrypoint="worker"`. Bundled registrations preserve
  the worker's restricted tool set; services and subscriptions do not start in worker processes.
- `@mb.tool` and `@mb.on(EventType)` decorator forms for extensions. `@mb.tool` derives the tool
  name from the function, the description from its docstring, and the JSON schema from the first
  argument's pydantic model, and hands the handler a validated model instead of a raw payload — a
  bad call reaches the model as `error_code: "invalid_tool_arguments"` so it can correct and retry.
  The explicit `mb.add_tool(ToolBinding(...))` and `mb.on(EventType, handler)` forms are unchanged
  and remain the escape hatch for hand-written schemas or non-identifier tool names.
- `pytest-timeout` as a dev dependency, applied to the event-bus, dispatcher and extension tests
  that can hang.

### Changed

- `[channels.<name>]` sections other than `telegram` are no longer silently validated *as* a
  Telegram config with every key dropped. `Settings.channels` is now a `ChannelsConfig` model:
  `telegram` stays validated, anything else is kept verbatim for the channel extension that owns it
  (`settings.channels.section("slack")`). Config files are unaffected — the TOML shape is identical,
  only the Python accessor changes from `settings.channels["telegram"]` to
  `settings.channels.telegram`.
- `minibot/app/daemon.py` and `minibot.app.console` no longer wire scheduler or task consumers by
  name. Bundled services own their startup/shutdown through the extension registry; the core tool
  factory now assembles only chat memory, calculator, skills, delegation, and extension bindings.

### Fixed

- OpenCode Go endpoints are detected from their base URL and skip the unsupported `/responses/compact` request, falling back directly to summary compaction.
- Agent-runtime `input_tokens` now reach session compaction, so repeated tool-loop calls count toward cost telemetry without triggering premature history compaction.
- Shutdown could deadlock: `EventSubscription.close()` and `EventBus.stop()` both used a blocking
  `put` for the stop sentinel, which never completes on a full queue — exactly the state a stalled
  subscriber leaves behind. The sentinel now evicts to make room.
- `EventSubscription.__aiter__` called `task_done()` only after the consumer resumed, so a
  subscriber that `break`s left the count unbalanced.

## [0.9.0] - 2026-09-09

### Fixed

- `minibot` on a fresh `pip install minibot` failed with `system_prompt_file configured but file
  not found: ./prompts/main_agent_system.md`. `[llm].system_prompt_file` and `prompts_dir` default
  to repo-relative `./prompts/...` paths that only existed in a git checkout. `prompts/**/*.md` is
  now shipped in the wheel/sdist, and `minibot configure` seeds a `prompts/` directory next to the
  config file it writes (skipped if one already exists there).

## [0.8.0] - 2026-09-08

### Added

- README "Quick start" section covering `pip install`, extras, `minibot configure`, and a Docker/
  `docker-compose` path.
- `minibot configure` now prompts for the task queue backend (`sqlite`/`rabbitmq`) when the tasks
  tool is enabled, instead of leaving `[tasks].backend` unset.

### Changed

- `[tasks].backend` now defaults to `sqlite` in `config.example.toml` and `config.yolo.toml`,
  since it needs no broker; `docker-compose.yml`'s `minibot-rabbitmq` service (and its
  `depends_on` entry) is commented out and only needed if you switch back to the rabbitmq backend.

### Fixed

- `minibot configure` on a fresh `pip install minibot` failed with `No such file or directory:
  .../site-packages/config.example.toml`. `config.example.toml` and `config.yolo.toml` were never
  shipped in the wheel/sdist, only referenced by a repo-root-relative path that happened to also
  resolve (but dangle) inside an installed package. Both files are now included via
  `pyproject.toml`'s `include` list, the same mechanism already used for the tool description
  `.txt` files.

## [0.7.0] - 2026-09-08

### Added

- SQLite task backend, selected with `[tasks].backend = "sqlite"`: a durable local queue with leasing,
  redelivery and retention, so async tasks no longer require running RabbitMQ. A task interrupted by a
  crash resumes on its own once the lease expires — the broker path could not do that. Configured
  under `[tasks.sqlite]`; `docker-compose.yml`'s RabbitMQ service is now optional.
- `core/tasks.py` defines a `TaskProducer` seam with two implementations, so `llm/tools/tasks.py` no
  longer imports `aio_pika`. The task tools now work without the `rabbitmq` extra installed.
- `lease_rows()` in `adapters/sqlalchemy_utils.py`, shared by the scheduler store and the new task
  store instead of duplicating the claim loop. Its conditional-`UPDATE` rowcount check is what makes a
  lease exclusive between concurrent leasers.
- `[tasks]` validator: on the sqlite backend `sqlite.lease_timeout_seconds` must exceed
  `worker_timeout_seconds`, otherwise a still-running task's lease expires and a second worker picks
  up the same row, running it twice and replying twice.

### Changed

- **Breaking, and silent.** Task settings moved from `[tools.tasks]` + `[rabbitmq]` into a single
  top-level `[tasks]` section (`enabled`, `backend`, `worker_timeout_seconds`,
  `max_concurrent_workers`), mirroring how `[scheduler.prompts]` already gates both its service and
  its tools. `[rabbitmq]` keeps only broker fields. Because only `Settings` sets `extra="forbid"`, a
  stale `[tools.tasks]` / `[rabbitmq].enabled` is **ignored rather than rejected** — and with no
  `[tasks]` section `enabled` defaults to `false`, so an unmigrated config boots clean with the task
  system silently switched off. Add `[tasks]` when upgrading.
- `adapters/tasks/worker.py` reads `settings.tasks.worker_timeout_seconds` instead of reaching into
  `settings.rabbitmq` — the worker is backend-agnostic and would otherwise have read the wrong field
  under the sqlite backend.
- `aiopipe` moved out of the `rabbitmq` extra into the core dependencies; it is the generic worker's
  IPC, used by both backends.

### Fixed

- `minibot console` never started a task consumer, so `spawn_task` there enqueued rows that nothing
  would ever run. It now builds the same consumer the daemon does.

## [0.6.0] - 2026-09-06

### Added

- `timeout_seconds` agent frontmatter field: a per-agent wall-clock budget overriding `orchestration.default_timeout_seconds`, so a long-running research agent no longer shares one global timeout with agents that answer in seconds.
- A delegated agent that times out now reports the work it completed before the cut (its last assistant messages and tool results) instead of a bare "timed out" with `total_tokens=0`. `AgentRuntime.run` mutates its state in place, so the transcript survives the `TimeoutError`; the tool-call retry path is tracked so the salvage covers it too.
- `ToolInputError` (`minibot/shared/errors.py`): a `ValueError` subclass carrying a structured `error_code`, so the tool executor classifies a failure from a typed field instead of matching on message text.

### Changed

- `[tools.tool_output_spill].exclude_tools` no longer excludes `bash`. `http_request` stays excluded because it runs a spill of its own and `pre_response` because it is signalling; `bash` had neither, only a 128KB inline truncation — and since `bash` is how a browser CLI runs, one oversized snapshot then rode along in the context of every later step of a tool loop. Measured on one prospecting sweep, the same task with the same tool-call count went from 1,448,896 tokens to 572,286, and the per-step peak from 111.7k to 44.8k.
- `[tools.skills].preload_catalog` now defaults to `true`. With it off the prompt only told the model to call `list_skills`, so it could not tell whether a relevant skill existed — while the specialist roster *is* embedded in the prompt with descriptions. Faced with that asymmetry the model delegated to a specialist for work a skill covered.
- `minibot configure` now derives `main_responses_state_mode` / `agent_responses_state_mode` from the selected provider (`previous_response_id` for `openai_responses`, which keeps turn state server-side; `full_messages` for stateless Chat Completions), and sets `preload_catalog` when skills are enabled.
- `resolve_existing_file` and `resolve_dir` (`minibot/adapters/files/local_storage.py`) now raise `ToolInputError` naming the offending path and the next step, with `file_not_found` / `path_is_not_a_file` / `folder_not_found` codes, instead of a bare `ValueError("file does not exist")`. Both sit on the path every file tool routes through, so a model that hit one no longer retries the identical call until a guardrail stops it.
- `aiosonic` is now locked at `1.0.6`, fixing the bare `AssertionError` raised when an HTTP status-line header has no reason phrase.
- `config.example.toml` and `config.yolo.toml` aligned with the two defaults above; `config.yolo.toml` previously carried neither section.
- `omit_temperature` and `timeout_seconds` agent frontmatter fields documented in `docs/agents.rst` and `ARCHITECTURE.md`.

### Fixed

- `http_request` returned `{"error": str(exc)}`, which is the empty string for a bare `AssertionError`, leaving the model with no reason for the failure. It now returns `ok`, `error_code`, the URL and a failure signature — and because the payload previously lacked `ok: False` it did not match the tool contract, so the repeated-failure guardrail never applied to it and an agent could retry a broken URL indefinitely.
- `test_bash_truncates_output_when_over_limit` shelled out to `python`, which is not a binary on systems shipping only `python3`; there it got exit 127 and failed on an unrelated truncation assertion. It now runs `sys.executable`.

## [0.5.0] - 2026-09-05

### Added

- `minibot configure`: an interactive terminal wizard (`minibot/adapters/config/configurator.py`) that creates or updates `config.toml`, covering runtime (log level/environment), Telegram (bot token, allowed chat/user IDs), LLM provider/model/API key (with `openai`, `openai_responses`, xAI, z.ai GLM Coding Plan, and OpenCode Zen/Go presets), and tool enablement, using arrow-key single- and multi-select prompts. New files start from the `example` or `yolo` config template; secrets are entered hidden and the target file is backed up before being overwritten.
- The configurator's model prompt fetches the live model list from the chosen provider's `/models` endpoint (working unauthenticated when no API key is set yet) and offers arrow-key selection, falling back to manual entry if the endpoint can't be listed.
- New `tomlkit` and `prompt-toolkit` dependencies backing the configurator's TOML round-tripping and interactive prompts.
- Scheduler `recurrence_type="cron"` (via `croniter`) for scheduled prompts, alongside the existing fixed-interval recurrence.
- `PendingTurnStore` (`minibot/adapters/memory/pending_turns.py`): persists in-flight message turns so a crash or restart mid-turn replays the message instead of silently dropping the reply.
- Tool output spill generalized: `apply_tool_output_spill` (`minibot/llm/tools/output_spill.py`) now wraps every tool binding (main agent, delegated agents, task workers), swapping oversized results for a managed-file pointer plus preview instead of just `http_client`.
- Browser automation now drives a `playwright-cli` agent skill through `bash`, replacing the `@playwright/mcp` server.
- HTML responses from `http_request` can render as compact, accessibility-tree-style text (`minibot/shared/html_compact.py`) via a new `"compact"` `response_processing_mode`.
- OpenCode Zen/Go and z.ai GLM Coding Plan example provider blocks added to `config.example.toml`/`config.yolo.toml` and the `ProviderConfig` docstring.

### Changed

- The persistent `memory` tool replaces title-based `save` with explicit `create` and ID-only `update`; `get` and `delete` now also require an `entry_id`. Agents must discover IDs with `search` or `list_titles` before mutating an entry.
- Memory entries now use one validated category: `finanzas`, `recordatorios`, `proyectos`, `preferencias`, `salud`, `viajes`, `vehículos`, `contactos`, `seguimiento`, `conocimiento`, or `otros`. `search` and `list_titles` support category, source, and update-date filters.
- No `ALTER TABLE` or schema migration is required: categories remain in the existing `metadata` JSON column. Existing installations only need the one-time data backfill described in the release notes for their stored entries.
- `poetry run minibot-console` is replaced by the `minibot console` subcommand, dispatched from `minibot/app/daemon.py` alongside the new `minibot configure`; `minibot/adapters/config/loader.py` now exposes a shared `resolve_config_path()` helper used by both the loader and the configurator.
- Docs (`docs/getting_started.rst`, `ARCHITECTURE.md`) updated for `minibot configure` and the `minibot console` subcommand.
- `response_processing_mode = "auto"` now renders HTML as compact text by default instead of the older plain-text extractor.
- Skill frontmatter parsing relaxed: the skill-name pattern now allows any characters except path separators/newlines, and frontmatter is parsed with a dedicated scalar-only parser (`parse_skill_frontmatter`) instead of the general YAML frontmatter parser, avoiding failures on non-scalar frontmatter in `SKILL.md` files.

### Fixed

- A turn where the model returned no visible text and no tool calls no longer gets silently dropped; both the interactive and scheduled-task-worker paths now fall back to a short reply.
- Provider quota/billing rejections are now classified via a typed `ProviderHTTPError` from the structured HTTP error body instead of generic failure text, including a dedicated `delegated_agent_quota_exceeded` error code from `agent_delegate`.
- Token-limit autoconfig no longer caps a delegated agent's `max_new_tokens` at the main chat model's configured value when the agent doesn't set its own.
- `html_compact`'s recursion guard now increments depth correctly through transparent wrapper tags (`div`/`span`), preventing a stack overflow on deeply nested markup; `html_to_compact` also catches `RecursionError` and falls back to the plain-text extractor.
- The `http_request` spill-failure notice now reflects the actual reason a managed-file write didn't happen instead of always blaming `max_spill_bytes`.

## [0.4.0] - 2026-04-25

### Added

- RAG support backed by Qdrant, with `rag_index`, `rag_search`, `rag_list_metadata`, and `rag_delete` tools for indexing files, searching chunks, discovering facet values, and deleting indexed data by explicit filters.
- `minibot/adapters/qdrant/AsyncQdrantClient`: async Qdrant HTTP client built on `aiosonic`, with collection bootstrap, point upsert, filtered delete, metadata facet listing, and vector search.
- `minibot/rag/` package for token-aware chunking, document ingestion, sentence-transformer embeddings, optional cross-encoder reranking, and retrieval orchestration.
- PDF ingestion for RAG via optional `pypdf`, with page markers in extracted text and startup/docs wiring for the extra dependency.
- Metadata-aware RAG filters and payloads, including `filename`, `document_id`, `user_id`, `agent_id`, `chat_id`, `tags`, and `categories`.
- `[tools.rag]` config block with embedding, rerank, and result-truncation settings, plus docs for setup, usage, and collection resets.
- RAG dependencies remain manual outside Poetry extras: install `torch` and `sentence-transformers`, and add `pypdf` support via `poetry install --all-extras`.
- RAG setup assets: `docs/rag.rst`, `scripts/rag_clear_collection.sh`, and a disabled-by-default Qdrant service in `docker-compose.yml`.

### Changed

- `bash` can now spill oversized output into a managed temp file and return preview/path metadata instead of forcing large responses inline.
- RAG chunking now uses embedding-token budgets instead of character counts, and the default embedding model changed to `sentence-transformers/all-MiniLM-L12-v2`.
- `rag_search` can optionally rerank a larger semantic candidate set with a cross-encoder before returning final results, and can truncate returned context to a configured token budget.
- Provider debug logging now records response metadata and tool-call names without logging raw provider payloads.

### Fixed

- Re-indexing a document now deletes stale chunks before upserting replacements, including empty re-index passes.
- RAG payload metadata now stores the resolved `filename`, and chunk IDs include scope values to avoid cross-scope collisions.
- RAG indexing rejects binary / non-UTF-8 inputs and enforces managed-root access rules unless file storage explicitly allows outside-root paths.
- Runtime-bound RAG scope filters now reject explicit `user_id`, `agent_id`, or `chat_id` values that do not match the active context.

## [0.3.0] - 2026-04-21

### Added

- `wait` tool: pauses execution for a given number of milliseconds (clamped to `[tools.wait].max_milliseconds`, default 30 000 ms). Useful for browser automation and human-paced interaction flows.
- `minibot-create-tool` Agent Skill wizard under `.agents/skills/minibot-create-tool/`: guided Q&A that scaffolds all required files (tool class, description `.txt`, config model, factory registration) for a new LLM tool.
- Sphinx documentation site under `docs/`, covering getting started, agents, architecture, configuration, tools, scheduler, audio transcription, MCP, security, and prompt packs.
- GitHub Pages deployment workflow for the Sphinx docs, with warning-as-error builds and rebuild triggers for docs, metadata, config examples, and documentation inputs.
- Grouped public tool surface table and `[tools.*]` configuration reference in the generated docs.
- Scheduler wake-on-startup: on `start()`, the nearest pending job is queried and an early wake task is scheduled so jobs due immediately after a restart are not delayed by the full poll interval.
- `get_nearest_pending_run_at()` added to `ScheduledPromptRepository` protocol and `SQLAlchemyScheduledPromptStore`, returning the minimum `run_at` across all pending jobs.
- `list_skills` tool for live skill discovery from disk, with optional query ranking and fuzzy fallback before loading full instructions via `activate_skill`.
- `[tools.skills].preload_catalog` config flag to optionally embed a prompt-time snapshot of skill names/descriptions while keeping `list_skills` as the live refresh path.
- Skill catalog fallback in system prompt: when `activate_skill` is available but `list_skills` is not attached (e.g. filtered by tool policy), the embedded skill catalog from `SkillRegistry.prompt_catalog()` is injected directly so the model has valid skill names without needing dynamic discovery.

### Changed

- README slimmed down to project summary, feature list, demo images, and a link to the full documentation site.
- Tool and config documentation now lives in Sphinx docs instead of standalone `tools/*.md` files.
- Package metadata now points `documentation` at the GitHub Pages documentation site.
- Agent documentation guidance now allows public docstrings when they feed generated docs or clarify public config/tool surfaces.
- Scheduler batch dispatch is now concurrent: `run_pending()` uses `asyncio.gather` so a slow publish no longer blocks other jobs in the same batch; unexpected per-job exceptions are logged individually.
- Scheduler retry delay now includes a `random.uniform(0, 10)` second jitter to prevent thundering-herd retries across a failing batch.
- Skill support no longer embeds the full catalog by default; the main prompt points to `list_skills`, and `activate_skill` remains responsible for loading full skill bodies on demand.

### Removed

- Legacy `tools/*.md` documentation files after migrating the public tool reference into Sphinx.
- Remaining Lua/Lupa config documentation and loader compatibility remnants.

### Fixed

- Scheduler no longer silently skips missed recurrence intervals; a `WARNING` is logged with `job_id` and the number of skipped intervals when the scheduler catches up after a gap.
- Scheduler startup now schedules the nearest wake before spawning the background loop, avoiding a partially running service if initial wake lookup fails.

### Added

- `pre_response` tool: agents call this before their final plain-text answer to declare `kind`, `meta`, and `attachments` metadata. Replaces the structured `AssistantRuntimePayload` JSON schema as the mechanism for conveying render hints and file attachments.
- `shell_agent` specialist (`agents/shell_agent.md`) using `gpt-5.4-nano` with high reasoning for bash and filesystem tasks.
- Async task execution over RabbitMQ: new `spawn_task`, `cancel_task`, and `list_tasks` tools plus `RabbitMQConsumerService`, `TaskManager`, and an isolated subprocess worker that can run specialist agents with a restricted tool allowlist, retry on provider rate limits, and emit message / file events back into the main event bus.
- Optional task-system configuration under `[tools.tasks]` and `[rabbitmq]`, plus a `rabbitmq` Poetry extra (`aio-pika`, `aiopipe`) and Docker/dev wiring for a local RabbitMQ service.

### Changed

- Agent runtime continuation is now driven purely by the tool-call loop: any step with tool calls continues; a step with no tool calls is the final answer. The outer `should_continue` / `_resolve_continuations` loop is removed entirely.
- `browser_agent.md` system prompt rewritten to remove the old structured JSON output contract (`answer`, `should_continue`, `attachments`); screenshot attachments are now declared via `pre_response` before the final answer. `pre_response` added to `tools_allow`.
- `count_tool_messages` (used by `RuntimeOrchestrationService` guardrail checks and delegated tool-use enforcement) now excludes `pre_response` calls from the count, so a turn that only calls `pre_response` is not treated as having executed a real tool.
- Container / daemon wiring now instantiates the task manager and RabbitMQ consumer only when `rabbitmq.enabled` is set, and tool assembly exposes task tools only when both RabbitMQ and `[tools.tasks]` are enabled.
- Telegram channel guidance now tells agents to answer with direct renderable text instead of the old `{"answer": ...}` wrapper, while `response_parser` still accepts the legacy structured answer shape for compatibility during migration.

### Removed

- Structured output infrastructure: `AssistantRuntimePayload` response schema, `main_assistant_response_model` / `main_assistant_response_schema`, `RuntimeStructuredOutputValidator`, `_DelegatedPayload`, and all `response_schema` / `local_response_model` parameters from `generate()` call sites.
- `ratchet-sm` runtime dependency: `ToolGuardrailValidator` rewritten with plain JSON parsing and Pydantic validation; `has_pseudo_tool_call_tag` inlined.
- `structured_output_mode` config field and its `provider_with_fallback` / `prompt_only` / `provider_strict` variants removed from `[llm]` config, `config.example.toml`, and README.

### Fixed

- `provider_factory._complete` was calling `self._provider.complete(...)` instead of `acomplete`; fixed to use the correct async method, resolving an `AttributeError` at runtime.
- Default installs without the `rabbitmq` extra no longer fail on startup when RabbitMQ is disabled; optional RabbitMQ / task imports are now deferred behind the feature flag.
- RabbitMQ dispatch now guards task-spawn failures so messages are requeued and semaphore permits are not leaked, and Telegram task attachments reject paths that escape the managed files root.

## [0.2.0] - 2026-04-07

### Added

- xAI native tool support for the `openai_responses` provider: `web_search` and `x_search` tools are injected at the provider level when `llm.xai.web_search_enabled` / `llm.xai.x_search_enabled` are set and the `base_url` resolves to `api.x.ai`. Domain/handle allow- and block-lists, image/video understanding flags, and date-range filters are all configurable under `[llm.xai.web_search]` and `[llm.xai.x_search]`.
- Runtime capability hints injected into the system prompt each turn: the model is told which provider-native tools (e.g. web search, X search) are active, and whether agent delegation is available.
- `provider_tool_calls` metric tracked end-to-end: usage parsing → `LLMGeneration` → `AgentRuntime` → `AgentRuntimeResult` → `SessionStateService`. Provider-native tool calls are now counted alongside local tool calls for guardrail bypass and session-state reporting.
- Structured logging for each agent runtime provider step (start, completion, failure) including `duration_ms`, provider name, and step index; same for delegated agent invocations.
- `PatchedOpenAIResponsesProvider` subclass that extends `OpenAIResponsesProvider` to support mixed tool lists (standard `Tool` objects alongside raw native-tool dicts).
- `provider_target.py`: URL-based provider resolution (`resolve_target_provider`, `infer_provider_from_base_url`) to detect xAI, OpenRouter, or OpenAI from `base_url`.
- `provider_capabilities.py`: builds provider-native tool payloads and capability hint strings from config.
- HTTP client large-response spill support: `[tools.http_client]` can now save oversized responses to managed temp files via `spill_to_managed_file`, `spill_after_chars`, `spill_preview_chars`, `max_spill_bytes`, and `spill_subdir`; spilled responses return a preview plus `body_notice`, `body_file_path`, `body_file_absolute_path`, and `body_file_bytes_written` so file/grep tools can inspect the full response.
- Public tool documentation under `tools/`, including a tool index plus purpose, configuration, interface, and safety notes for each canonical MiniBot tool.

### Changed

- Config loader now distinguishes files from directories: explicit paths that point to a directory raise `ValueError` instead of falling back to defaults; default-path candidates that are directories are skipped.
- Guardrail is skipped when `provider_tool_calls > 0` (not just when local tool messages exist), preventing redundant guardrail evaluation after provider-side tool use.
- Continuation loop in `RuntimeOrchestrationService` coerces a visible `should_continue=true` response to final when no remaining work is detectable (no local tool messages and no provider tool calls), avoiding infinite loops on provider-native-tool-only turns.
- `browser_agent.md` no longer hard-codes a model/provider; agent model selection falls back to the orchestration default.
- Main-agent prompt policy is leaner, with tool-use and delegation guidance moved into focused prompt fragments instead of being duplicated in the base system prompt.

### Removed

- Lua/lupa integration removed: `config.lua` support, `[tools.lua_custom]`, the `lua` extras group, and the `minibot-config toml-to-lua` CLI command. The added complexity was not justified at this stage of the project. Dynamic config via Python may be revisited in a future release.

### Fixed

- `delegated_timeout` error payload now includes `provider` and `model` fields for easier debugging.
- Responses API previous-response reuse is now tied to the system-prompt fingerprint, preventing stale `previous_response_id` state from being reused after prompt/capability changes.
- Structured-output schema fallback now catches additional provider rejection shapes such as invalid `response_format` schemas and unsupported `allOf` usage.

## [0.1.1] - 2026-03-15

### Changed

- Version bump to work around PyPI filename reuse restriction on `0.1.0`.

## [0.1.0] - 2026-03-15

### Changed

- `StructuredOutputValidator` rewritten to own the validation loop directly (no longer delegates to `ratchet_sm`'s `StateMachine`): parse-error and schema-validation failures now emit structured `RetryAction` with per-attempt prompt patches via `ValidationFeedback` / `_render_schema_retry_prompt` / `_render_pydantic_retry_prompt`, giving richer normalization feedback to the LLM on each retry.
- Dict-schema path now validates against `validate_json_schema_instance` and returns the raw parsed dict (preserving `null` values); Pydantic-model path validates via `model_validate` and serializes with `model_dump(exclude_none=True)`.

### Added

- Agent Skills support: load skill instruction files from `.agents/skills/` or `.claude/skills/` directories (project- and user-level); skills are listed in the system prompt and loaded on demand via the new `activate_skill` tool.
- `[skills]` config block with optional `paths` override.
- `minibot/shared/frontmatter.py` shared YAML frontmatter parser (reused by both agent and skill loaders).

### Fixed

- `SkillDefinitionConfig` now rejects unknown frontmatter keys (`extra="forbid"`), logging a warning and skipping the skill instead of silently ignoring typos.

## [0.0.9] - 2026-03-15

### Added

- New `general_agent` specialist (`agents/general.md`) for offloading simple and intermediate tasks with MCP tools excluded by default.
- Token-limit auto-configuration at startup (`minibot/app/token_limits_autoconfig.py`): fetches the models catalog from `models.dev` and automatically sets `max_history_tokens` and `max_new_tokens` for the main model and each agent spec based on real context/output limits.
- `structured_output_mode` config knob supporting `"prompt"` mode: when schema-based responses are not supported by a model, the schema is injected into the system prompt instead of being sent as a native response schema.
- New `minibot/llm/services/structured_output_policy.py` module with helpers to normalize output modes, decide whether to send a response schema natively, and augment system prompts with schema guidance.
- New `minibot/llm/services/reasoning_replay.py` module for replaying reasoning steps during structured-output retries.
- Agent name and description validation warnings in `agent_definitions_loader` (name pattern check, description length cap).
- `fetch_agent_info` tool description file added (`minibot/llm/tools/descriptions/fetch_agent_info.txt`).

### Changed

- `complete_with_schema_fallback` now applies provider-agnostic retry logic (previously OpenRouter-only): any provider can retry without `response_schema` and fall back to prompt-injected schema guidance.
- `generate_with_tools` now accepts `local_response_model` and `structured_output_mode` parameters; structured validator now uses the local Pydantic model when available.
- Structured validation `FailAction` now returns a deterministic user-facing fallback payload with `should_continue: false` instead of a raw dict dump.
- Agent runtime and delegation flow hardened: delegation trace, response parser, and runtime service were refined for cleaner structured-output handling and delegation result extraction.
- `browser_agent.md` output contract updated: `should_answer_to_user` field replaced by `should_continue`, and screenshot instructions generalized to tool-agnostic guidance.
- Delegation tools (`fetch_agent_info`, `invoke_agent`) descriptions updated; `list_agents` description removed.
- Main agent system prompt and channel prompts (console, Telegram) simplified.
- `prompts/policies/delegation.md` removed; delegation guidance consolidated into tool descriptions and the main system prompt.

### Fixed

- Schema fallback retry no longer leaks internal `_structured_output_prompt_schema` key to the provider call.
- Agent registry and dispatcher startup logging improved for enabled agents and tools.

## [0.0.8] - 2026-03-08

### Added

- Ratchet-backed structured output validation (`minibot/app/runtime_structured_output.py`) with schema-aware retries and deterministic fallback payloads.
- Runtime dependency `ratchet-sm[pydantic]` for structured output state-machine validation.
- Expanded runtime tests covering structured-output success, retry recovery, retry exhaustion fallback, step-budget retry behavior, and custom validator schemas.
- New `memory(action="list_titles")` operation for lightweight memory discovery (`id`, `title`, `updated_at`, `source`) with optional query filtering.
- Optional tool suite additions: `bash`, `apply_patch`, `grep`, and `transcribe_audio` (with dedicated tool descriptions and patch-engine support).
- New `code_read` managed-file tool for bounded line-window reads (`path`, `offset`, `limit`).
- Audio transcription runtime support via optional `faster-whisper` (`stt` extra) plus auto-transcribe of short incoming Telegram audio/voice attachments.
- Telegram incoming media mapper for consistent photo/document/audio/voice file naming and attachment metadata (including `duration_seconds` for audio inputs).
- New tool/config surfaces for `[tools.bash]`, `[tools.apply_patch]`, `[tools.grep]`, `[tools.audio_transcription]`, and `tools.file_storage.allow_outside_root`.
- `minibot-console --verbose` option to mirror runtime logs to stdout for local debugging.
- `config.yolo.toml`, `docker-requirements.txt`, and container runtime updates to support full-capability local stacks (including Playwright MCP and STT prerequisites).

### Changed

- `AgentRuntime` now validates structured final responses, retries invalid payloads with repair prompts, and returns safe fallback structured payloads after max attempts.
- Delegated-agent payload extraction now validates against strict Pydantic schemas instead of permissive coercion.
- Structured response parsing now requires strict `answer` object + boolean `should_answer_to_user` semantics.
- Canonical render kind was standardized from `markdown_v2` to `markdown` across channel schema, handler/prompt paths, and Telegram prompt guidance.
- Dispatcher/console startup logging now includes enabled main-agent tool names for observability.
- Memory search now uses a two-stage FTS strategy: strict token matching first, then relaxed matching when strict results are empty.
- File storage and filesystem tools now support optional yolo-mode path handling (absolute paths outside managed root), and tool responses include canonical path metadata (`path_relative`, `path_absolute`, `path_scope`).
- Handler runtime now tracks recent filesystem paths from tool outputs and injects recent path context into follow-up turns to improve tool argument accuracy.
- Tool-use guardrail classification now uses a ratchet-backed validator and is skipped when tools already executed in the same runtime pass.
- OpenRouter request building now carries reasoning `effort` in provider kwargs and auto-enables reasoning when effort is present.
- Environment prompt fragments now include cwd plus resolved filesystem root/path-mode guidance.
- Main handler orchestration was further decomposed into `LLMTurnService` plus focused collaborators for input, prompting, runtime execution, compaction, and metadata assembly.
- Telegram channel plumbing was split into dedicated authorization, incoming-media collection, and outbound-sender components to isolate responsibilities.
- LLM generation/runtime loops now apply targeted recovery for truncated tool arguments and pseudo tool-call tags before hitting fallback behavior.
- Tool registration now uses feature-based assembly and canonical tool-label reporting, and auto-enables `code_read` whenever file storage tooling is enabled.

### Fixed

- Console command startup no longer assumes a full `logging.Logger` interface in tests; info-level startup logging is now defensive for lightweight logger doubles.
- `memory(action="get")` misses by title now return `suggested_titles` when similar entries exist, improving memory recall and follow-up selection.
- Tool execution error payloads now include deterministic failure signatures, improving repeated-failure diagnostics in tool loops.
- Agent runtime now short-circuits repeated identical tool failures/iterations earlier, returning deterministic fallback payloads instead of looping.
- Runtime tool execution now normalizes legacy tool aliases (`http_client`, `calculator`, `datetime_now`, `artifact_insert`) to canonical names for compatibility.

## [0.0.7] - 2026-02-25

### Added

- Internal service modules for handler/runtime orchestration (`minibot.app.handlers.services.*`) and LLM request assembly/execution (`minibot.llm.services.*`) to separate state, compaction, tool-loop, schema, and usage concerns.
- Shared async retry helper at `minibot/shared/retries.py` reused by LLM/bootstrap paths.

### Changed

- Refactored `LLMMessageHandler` and surrounding runtime wiring to delegate state/metadata/input/prompt/runtime/compaction responsibilities to focused services while preserving behavior.
- Responses API state and compaction flow were tightened for clearer memory routing and more consistent compaction prompting/tool-usage guidance.
- Provider/client bootstrap path now applies transport behavior based on endpoint scheme, with HTTP/2 used only for HTTPS-capable endpoints.

### Fixed

- Local/non-HTTPS LLM endpoints (for example Ollama over plain HTTP) now avoid forced HTTP/2, reducing bootstrap/connection failures.

## [0.0.6] - 2026-02-20

### Added

- Packaged tool-description resources under `minibot/llm/tools/descriptions/*.txt` plus a cached `description_loader` for loading complex tool guidance from files.
- New unified `read_file` tool for reading UTF-8 text files from managed storage (with truncation metadata), complementing `filesystem` + `glob_files` workflows.
- File-based main-agent system prompt support via `prompts/main_agent_system.md` (`llm.system_prompt_file`), with startup fail-fast validation when configured.
- Optional main-agent guardrail plugin architecture with `[orchestration].main_tool_use_guardrail` (`"disabled"` by default, `"llm_classifier"` opt-in).
- Internal handler collaborators for clearer orchestration boundaries: `delegation_trace`, `response_parser`, and `incoming_files_context` modules.

### Changed

- Tool catalog simplified around unified action tools:
  - `filesystem` is now the primary file-operation surface (list/glob/info/write/move/delete/send).
  - `memory` is now the only exposed user-memory tool (sub-tools are internal-only).
  - `python_exec` and `history` unified wrappers were removed in favor of direct tools (`python_execute`, `python_environment_info`, `chat_history_info`, `chat_history_trim`).
- Delegation tools were simplified to `fetch_agent_info` and `invoke_agent`; the `agent_delegate` wrapper tool was removed.
- Main system prompt was simplified, with tool descriptions treated as the authoritative source for tool-specific behavior.
- Memory and delegation guidance was expanded so the model more proactively uses contextual memory lookup and specialist-agent discovery/delegation.
- Direct delete fallback in `LLMMessageHandler` now routes through `filesystem(action="delete")`.
- Telegram output now accepts normal Markdown and converts it to Telegram MarkdownV2 via `telegramify-markdown` at send time (with plain-text fallback on formatter errors).
- Compaction flow now preserves exactly two post-compaction memory entries (`user` compaction request + `assistant` summary) and can emit the generated summary in `compaction_updates`.
- Token trace semantics now distinguish pre-compaction totals from compaction-call usage and include guardrail-classifier token usage in session accounting.
- `LLMMessageHandler` runtime path was refactored to keep behavior while isolating guardrail/delegation parsing and retry orchestration.

### Removed

- Exposed granular file tools (`list_files`, `create_file`, `file_info`, `move_file`, `delete_file`, `send_file`) and the alias `artifact_insert` from the public tool surface.
- Exposed user-memory sub-tools (`user_memory_save`, `user_memory_get`, `user_memory_search`, `user_memory_delete`) and the `agent_delegate` tool.

### Fixed

- Guardrail retry path now preserves delegated unresolved-result handling, so bounded delegation fallback still applies when guardrail retry is active.
- Guardrail classifier token usage now contributes to session-level token counters used by compaction thresholds and token metadata.

## [0.0.5] - 2026-02-16

### Added

- Specialist-agent orchestration with file-defined agent specs (`./agents/*.md`), delegation tools (`fetch_agent_info`, `invoke_agent`, `agent_delegate`), and per-agent tool scoping for local tools + MCP servers.
- Console channel support (`minibot-console`) for REPL and one-shot local conversations through the same dispatcher/handler pipeline used by Telegram.
- Provider credential registry (`[providers.<name>]`) plus per-agent LLM overrides (provider/model/runtime params), including OpenRouter provider-routing and reasoning toggles.
- Optional token-aware conversation compaction controls (`memory.max_history_tokens`) with token tracing metadata and optional user-facing compaction updates.
- Unified action-style tool facades (`filesystem`, `history`, `memory`, `schedule`) alongside existing granular tools.

### Changed

- Configuration model now separates provider credentials from `[llm]`, adds `[orchestration]` and `[tools.browser]` blocks, and expands memory controls for history trimming/compaction behavior.
- Main-agent runtime now supports tool ownership modes (`shared`, `exclusive`, `exclusive_mcp`) so specialist-owned tools can be hidden from the main agent and accessed via delegation.
- Tool argument parsing and schema handling were standardized across built-in tools with stricter object schemas and shared validation helpers.
- MCP bridge integration now injects Playwright output-dir defaults, sanitizes `null` payload fields, and improves result shaping/logging for large or binary-heavy responses.
- LLM runtime metadata now includes richer token/delegation traces for observability and downstream channel handling.

### Fixed

- OpenAI strict function-schema compatibility for tool definitions (including MCP-exposed tools), reducing invalid function-parameter failures.
- OpenRouter requests now retry without response schemas when JSON-mode/schema errors are returned by incompatible models.
- MCP stdio transport now handles chunked output and ignores non-JSON lines, improving reliability with noisy MCP server stdout/stderr behavior.

## [0.0.4] - 2026-02-13

### Added

- MCP bridge tooling with stdio/HTTP transports, dynamic tool discovery, and namespaced MCP tool bindings.
- Channel prompt pack loading (`llm.prompts_dir`) so per-channel prompt fragments can be composed with the base system prompt.
- Managed file storage enhancements: glob-style file search plus recursive folder deletion controls.
- New runtime/LLM tuning knobs for agent timeout, request socket timeouts, and retry behavior.

### Changed

- Tooling and docs now position browser automation through MCP servers (for example Playwright MCP) instead of dedicated Playwright config.
- CI and local setup docs now standardize dependency install commands on `poetry install --all-extras`.
- Tool/config schema wiring was expanded for MCP server registration and stricter tool schema compatibility.

### Fixed

- Duplicate outbound message handling in dispatcher/channel flow.
- OpenAI tool schema strictness issues that could surface as invalid function parameter errors.

## [0.0.3] - 2026-02-10

### Added

- Python execution artifact flow: `python_execute` can now generate files and persist them into managed storage for later delivery.

### Changed

- Channel delivery flow now supports receiving Python-generated files in Telegram through managed file events (`send_file`/`OutboundFileEvent`).

## [0.0.2] - 2026-02-10

### Added

- Managed workspace file tooling behind `tools.file_storage` (`list_files`, `file_info`, `create_file`, `move_file`, `delete_file`, `send_file`, `self_insert_artifact`).
- New local file storage adapter with path-safety checks, write-size limits, and metadata-aware file listing.
- Agent runtime loop (`AgentRuntime`) with directive support, runtime limits, and managed-file rendering for multimodal providers.
- Channel models/events for file responses (`ChannelFileResponse`, `OutboundFileEvent`) and incoming media references (`IncomingFileRef`).

### Changed

- Telegram media handling now persists inbound photos/documents to managed temporary storage and passes file references through message metadata.
- Telegram outbound flow now supports sending files via `send_document` when tools emit outbound file events.
- LLM orchestration now supports step-wise runtime execution (`complete_once` + runtime tool execution path) and wires trusted directive tools in `LLMMessageHandler`.
- Tool factory wiring now injects event bus support for file tools and enables file-storage tool registration through config.

### Documentation

- Updated README, architecture docs, and example config with managed file workspace and file-storage tool configuration details.

## [0.0.1] - 2026-02-10

### Added

- First release.

[Unreleased]: https://github.com/sonic182/minibot/compare/0.21.0...HEAD
[0.21.0]: https://github.com/sonic182/minibot/compare/0.20.0..0.21.0
[0.20.0]: https://github.com/sonic182/minibot/compare/0.19.0..0.20.0
[0.19.0]: https://github.com/sonic182/minibot/compare/0.18.0..0.19.0
[0.18.0]: https://github.com/sonic182/minibot/compare/0.17.0..0.18.0
[0.17.0]: https://github.com/sonic182/minibot/compare/0.16.0..0.17.0
[0.16.0]: https://github.com/sonic182/minibot/compare/0.15.0..0.16.0
[0.15.0]: https://github.com/sonic182/minibot/compare/0.14.3..0.15.0
[0.14.3]: https://github.com/sonic182/minibot/compare/0.14.2..0.14.3
[0.14.2]: https://github.com/sonic182/minibot/compare/0.14.1..0.14.2
[0.14.1]: https://github.com/sonic182/minibot/compare/0.14.0..0.14.1
[0.14.0]: https://github.com/sonic182/minibot/compare/0.13.0..0.14.0
[0.13.0]: https://github.com/sonic182/minibot/compare/0.12.0..0.13.0
[0.12.0]: https://github.com/sonic182/minibot/compare/0.11.0..0.12.0
[0.11.0]: https://github.com/sonic182/minibot/compare/0.10.0..0.11.0
[0.10.0]: https://github.com/sonic182/minibot/compare/0.9.0..0.10.0
[0.9.0]: https://github.com/sonic182/minibot/compare/0.8.0..0.9.0
[0.8.0]: https://github.com/sonic182/minibot/compare/0.7.0..0.8.0
[0.7.0]: https://github.com/sonic182/minibot/compare/0.6.0..0.7.0
[0.6.0]: https://github.com/sonic182/minibot/compare/0.5.0..0.6.0
[0.5.0]: https://github.com/sonic182/minibot/compare/0.4.0..0.5.0
[0.4.0]: https://github.com/sonic182/minibot/compare/0.3.0..0.4.0
[0.3.0]: https://github.com/sonic182/minibot/compare/0.2.0..0.3.0
[0.2.0]: https://github.com/sonic182/minibot/compare/0.1.1..0.2.0
[0.1.1]: https://github.com/sonic182/minibot/compare/0.1.0..0.1.1
[0.1.0]: https://github.com/sonic182/minibot/compare/0.0.9..0.1.0
[0.0.9]: https://github.com/sonic182/minibot/compare/0.0.8..0.0.9
[0.0.8]: https://github.com/sonic182/minibot/compare/0.0.7..0.0.8
[0.0.7]: https://github.com/sonic182/minibot/compare/0.0.6..0.0.7
[0.0.6]: https://github.com/sonic182/minibot/compare/0.0.5..0.0.6
[0.0.5]: https://github.com/sonic182/minibot/compare/0.0.4..0.0.5
[0.0.4]: https://github.com/sonic182/minibot/compare/0.0.3..0.0.4
[0.0.3]: https://github.com/sonic182/minibot/compare/0.0.2..0.0.3
[0.0.2]: https://github.com/sonic182/minibot/compare/0.0.1..0.0.2
[0.0.1]: https://github.com/sonic182/minibot/releases/tag/v0.0.1
