# activate_skill

## Purpose

Loads full instructions for a discovered skill so the model can follow that skill's workflow.

## Availability

Enabled when `[tools.skills].enabled = true`.

## Configuration

Relevant config: `[tools.skills]`.

Important field: `paths`. Leave it empty to use default discovery paths or set it to replace defaults entirely.

## Interface

Inputs:

- `name`: exact skill name returned by `list_skills`.

The response includes full skill instructions, the skill directory, and supporting resource information.

## Typical Flow

1. Call `list_skills` to discover the current available skills.
2. Choose a returned skill name.
3. Call `activate_skill` with that exact name.

## Safety Notes

Skill instructions can direct the model to read additional local resources. Configure `paths` deliberately to control which skill packs are discoverable.
