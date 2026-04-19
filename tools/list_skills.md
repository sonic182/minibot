# list_skills

## Purpose

Discovers the current skills available on disk and returns their names and descriptions.

## Availability

Enabled when `[tools.skills].enabled = true`.

## Configuration

Relevant config: `[tools.skills]`.

Important fields:

- `paths`: leave empty to use default discovery paths or set it to replace defaults entirely.
- `preload_catalog`: when true, the main prompt also includes a prompt-time snapshot of skill names/descriptions.

## Interface

Inputs:

- `query` (optional): search text used to filter and rank skills by name or description.

The response includes the matching skills currently available from disk, along with their descriptions.

## Matching Behavior

- Exact name matches rank first.
- Prefix and substring matches rank before fuzzy matches.
- When direct matches are weak or absent, the tool falls back to fuzzy ranking for typo tolerance.
- The result is a discovery list only; use `activate_skill` with the exact returned name to load full instructions.

## Safety Notes

`list_skills` reflects the configured discovery paths at call time, so newly added or deleted `SKILL.md` files can appear or disappear without restarting MiniBot. Configure `paths` deliberately to control which skill packs are discoverable.

If `preload_catalog` is enabled, that prompt catalog is only a snapshot. Use `list_skills` when live state matters.
