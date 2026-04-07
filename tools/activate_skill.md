# activate_skill

## Purpose

Loads full instructions for a discovered skill so the model can follow that skill's workflow.

## Availability

Enabled when `[tools.skills].enabled = true` and the skill registry has discovered at least one skill.

## Configuration

Relevant config: `[tools.skills]`.

Important field: `paths`. Leave it empty to use default discovery paths or set it to replace defaults entirely.

## Interface

Inputs:

- `name`: exact skill name from the available skills list.

The response includes full skill instructions, the skill directory, and supporting resource information.

## Safety Notes

Skill instructions can direct the model to read additional local resources. Configure `paths` deliberately to control which skill packs are discoverable.
