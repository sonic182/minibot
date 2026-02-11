from __future__ import annotations

DICE_TOOL_NAME = "roll_dice"
DICE_TOOL_DESCRIPTION = "Roll a dice with a configurable number of sides."
DICE_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "sides": {"type": "integer", "minimum": 2, "default": 6},
        "seed": {"type": ["integer", "null"]},
    },
    "required": [],
    "additionalProperties": False,
}
