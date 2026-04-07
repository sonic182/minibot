# filesystem

## Purpose

Provides a unified managed-file action facade for common file operations.

## Availability

Enabled by `[tools.file_storage].enabled = true`.

## Configuration

Relevant config: `[tools.file_storage]`.

Important fields include `root_dir`, `max_write_bytes`, `allow_outside_root`, `save_incoming_uploads`, `uploads_subdir`, and `incoming_temp_subdir`.

## Interface

Inputs:

- `action`: one of `list`, `glob`, `info`, `write`, `move`, `delete`, or `send`.
- `folder`, `pattern`, `limit`: used for `list` or `glob`.
- `path`, `content`, `overwrite`: used for `info`, `write`, `delete`, or `send`.
- `source_path`, `destination_path`, `overwrite`: used for `move`.
- `target`, `recursive`: used for `delete`.
- `caption`: used for `send`.

## Safety Notes

Paths are managed relative to `tools.file_storage.root_dir` unless `allow_outside_root` is explicitly enabled. `send` requires channel, chat, and event-bus context.
