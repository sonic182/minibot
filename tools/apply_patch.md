# apply_patch

## Purpose

Applies structured file edits using the opencode-style patch envelope.

## Availability

Enabled by `[tools.apply_patch].enabled = true`.

## Configuration

Relevant config: `[tools.apply_patch]`.

Important fields include `restrict_to_workspace`, `workspace_root`, `allow_outside_workspace`, `preserve_trailing_newline`, and `max_patch_bytes`.

## Interface

Inputs:

- `patch_text`: full patch text including `*** Begin Patch` and `*** End Patch`.

The patch format supports add, update, delete, and move operations. The result includes an update summary, updated file list, and resolved workspace root.

## Safety Notes

Keep `restrict_to_workspace = true` unless unrestricted file edits are intentionally required.
