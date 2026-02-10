# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/sonic182/minibot/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/sonic182/minibot/releases/tag/v0.0.1
