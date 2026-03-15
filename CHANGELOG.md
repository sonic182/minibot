# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Agent Skills support: load skill instruction files from `.agents/skills/` or `.claude/skills/` directories (project- and user-level); skills are listed in the system prompt and loaded on demand via the new `activate_skill` tool.
- `[skills]` config block with optional `paths` override.
- `minibot/shared/frontmatter.py` shared YAML frontmatter parser (reused by both agent and skill loaders).
- Optional Lua integration (`lupa` extra): load a dynamic `config.lua` instead of static `config.toml`, enabling programmatic config generation (environment-dependent values, computed fields, conditional sections); define custom LLM tools as Lua scripts, each running in its own `LuaRuntime` instance offloaded to a thread executor to avoid blocking the event loop.

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

[Unreleased]: https://github.com/sonic182/minibot/compare/0.0.9..HEAD
[0.0.9]: https://github.com/sonic182/minibot/compare/0.0.8..0.0.9
[0.0.8]: https://github.com/sonic182/minibot/compare/0.0.7..0.0.8
[0.0.7]: https://github.com/sonic182/minibot/compare/0.0.6..0.0.7
[0.0.6]: https://github.com/sonic182/minibot/compare/0.0.5..0.0.6
[0.0.5]: https://github.com/sonic182/minibot/compare/0.0.4..0.0.5
[0.0.4]: https://github.com/sonic182/minibot/compare/0.0.3..0.0.4
[0.0.3]: https://github.com/sonic182/minibot/compare/0.0.2..0.0.3
[0.0.2]: https://github.com/sonic182/minibot/compare/0.0.1..0.0.2
[0.0.1]: https://github.com/sonic182/minibot/releases/tag/v0.0.1
