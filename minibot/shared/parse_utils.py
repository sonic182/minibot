from __future__ import annotations

import ast
import json
import re
from typing import Any


def parse_json_maybe_python_object(payload: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(payload)
        except (ValueError, SyntaxError):
            return None
    if isinstance(parsed, dict):
        return dict(parsed)
    return None


def parse_json_with_fenced_fallback(payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        stripped = payload.strip()
        stripped = re.sub(r"^```json\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"^```\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
        return json.loads(stripped)
