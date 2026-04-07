# grep

## Purpose

Searches managed files with regex or fixed-string matching.

## Availability

Enabled by `[tools.grep].enabled = true`. It requires `[tools.file_storage].enabled = true`.

## Configuration

Relevant config: `[tools.grep]`.

Important fields include `max_matches` and `max_file_size_bytes`.

## Interface

Inputs:

- `pattern`: regex or fixed pattern to search.
- `path`: optional file or folder path; defaults to managed root.
- `recursive`: whether to search folders recursively; defaults to true.
- `ignore_case`: case-insensitive matching.
- `fixed_string`: treat `pattern` literally.
- `include_hidden`: include hidden files and folders.
- `context_before`, `context_after`: line context around matches.
- `max_matches`: optional per-call match cap.

The result includes matched lines, file scan counts, skipped files, and truncation status.

## Safety Notes

Files larger than `max_file_size_bytes` are skipped.
