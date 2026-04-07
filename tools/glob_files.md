# glob_files

## Purpose

Lists managed files matching a glob pattern.

## Availability

Enabled by `[tools.file_storage].enabled = true`.

## Configuration

Relevant config: `[tools.file_storage]`, especially `root_dir` and `allow_outside_root`.

## Interface

Inputs:

- `pattern`: glob pattern such as `**/*.md` or `uploads/**/*.png`.
- `folder`: optional folder relative to managed root.
- `limit`: optional maximum number of matches.

The result includes root, folder, pattern, entries, and count.

## Safety Notes

Search is scoped to managed storage unless file-storage confinement is intentionally relaxed.
