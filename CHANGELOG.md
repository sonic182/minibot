# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Managed file workspace support via new `tools.file_storage` config (`root_dir`, `max_write_bytes`, `max_read_bytes`, `max_read_lines`).
- File storage domain contract and models in `minibot/core/files.py`.
- Local filesystem-backed storage adapter in `minibot/adapters/files/local_storage.py` with path-sandboxing and size limits.
- New LLM tools: `file_write`, `file_list`, and `file_read`.
- New channel tool: `send_file_in_channel` to publish file media events from managed storage.
- New channel media response/event models (`ChannelMediaResponse`, `OutboundMediaEvent`) and Telegram media send path.
- Added dedicated coverage for file storage tools and channel file sender behavior.

### Changed

- `AppContainer` now wires optional file storage when `tools.file_storage.enabled` is true and exposes `get_file_storage()`.
- Tool factory now conditionally registers file storage tools and channel file sender when dependencies are available.
- Dispatcher now passes `event_bus` and `file_storage` into tool construction.
- Telegram service now handles outbound media events and sends `photo`/`document` payloads.
- Extended configuration, container, dispatcher, tool factory, and Telegram authorization tests for file storage/media event flows.

## [0.0.1] - 2026-02-10

### Added

- First release.

[0.0.1]: https://github.com/sonic182/minibot/releases/tag/v0.0.1
