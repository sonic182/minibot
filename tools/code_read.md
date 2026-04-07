# code_read

## Purpose

Reads a bounded line window from a managed text file.

## Availability

Enabled by `[tools.file_storage].enabled = true`.

## Configuration

Relevant config: `[tools.file_storage]`, especially `root_dir` and `allow_outside_root`.

## Interface

Inputs:

- `path`: relative file path under managed root.
- `offset`: zero-based starting line offset, defaulting to `0`.
- `limit`: number of lines to return, defaulting to `200` and clamped to `400`.

The result includes line-window metadata and file content for the selected range.

## Safety Notes

Use this instead of `read_file` when a file may be large or line context matters.
