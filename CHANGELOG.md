# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Packaged tool-description resources under `minibot/llm/tools/descriptions/*.txt` plus a cached `description_loader` for loading complex tool guidance from files.
- New unified `read_file` tool for reading UTF-8 text files from managed storage (with truncation metadata), complementing `filesystem` + `glob_files` workflows.

### Changed

- Tool catalog simplified around unified action tools:
  - `filesystem` is now the primary file-operation surface (list/glob/info/write/move/delete/send).
  - `memory` is now the only exposed user-memory tool (sub-tools are internal-only).
  - `python_exec` and `history` unified wrappers were removed in favor of direct tools (`python_execute`, `python_environment_info`, `chat_history_info`, `chat_history_trim`).
- Delegation tools were simplified to `list_agents` and `invoke_agent`; the `agent_delegate` wrapper tool was removed.
- Main system prompt was simplified, with tool descriptions treated as the authoritative source for tool-specific behavior.
- Memory and delegation guidance was expanded so the model more proactively uses contextual memory lookup and specialist-agent discovery/delegation.
- Direct delete fallback in `LLMMessageHandler` now routes through `filesystem(action="delete")`.

### Removed

- Exposed granular file tools (`list_files`, `create_file`, `file_info`, `move_file`, `delete_file`, `send_file`) and the alias `artifact_insert` from the public tool surface.
- Exposed user-memory sub-tools (`user_memory_save`, `user_memory_get`, `user_memory_search`, `user_memory_delete`) and the `agent_delegate` tool.

## [0.0.5] - 2026-02-16

### Added

- Specialist-agent orchestration with file-defined agent specs (`./agents/*.md`), delegation tools (`list_agents`, `invoke_agent`, `agent_delegate`), and per-agent tool scoping for local tools + MCP servers.
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

[Unreleased]: https://github.com/sonic182/minibot/compare/0.0.5..HEAD
[0.0.5]: https://github.com/sonic182/minibot/compare/0.0.4..0.0.5
[0.0.4]: https://github.com/sonic182/minibot/compare/0.0.3..0.0.4
[0.0.3]: https://github.com/sonic182/minibot/compare/0.0.2..0.0.3
[0.0.2]: https://github.com/sonic182/minibot/compare/0.0.1..0.0.2
[0.0.1]: https://github.com/sonic182/minibot/releases/tag/v0.0.1
