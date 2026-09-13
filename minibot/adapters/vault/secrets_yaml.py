from __future__ import annotations

import json

# ponytail: flat `name: value` only, no block scalars. A multi-line secret (a PEM key) round-trips
# correctly as a single JSON-quoted line; add `|` support if editing those becomes unpleasant.

_NEEDS_QUOTING_PREFIXES = ('"', "'", "#", "-")


def loads(text: str) -> dict[str, str]:
    """Parse the flat ``name: value`` secrets document.

    Values are **never** type-coerced: every value is returned as ``str``, so an all-numeric API
    key or a literal ``true`` survives intact.
    """
    secrets: dict[str, str] = {}
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"invalid secrets line {number}: expected `name: value`")
        name, _, value = line.partition(":")
        name = name.strip()
        if not name:
            raise ValueError(f"invalid secrets line {number}: empty secret name")
        if name in secrets:
            raise ValueError(f"duplicate secret name on line {number}: {name!r}")
        secrets[name] = _parse_value(value.strip(), number)
    return secrets


def dumps(secrets: dict[str, str]) -> str:
    lines = [f"{name}: {_format_value(value)}" for name, value in secrets.items()]
    return "\n".join(lines) + "\n" if lines else ""


def _parse_value(text: str, number: int) -> str:
    if text.startswith('"'):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid quoted value on line {number}: {exc}") from exc
        if not isinstance(decoded, str):
            raise ValueError(f"invalid quoted value on line {number}: expected a string")
        return decoded
    return text


def _format_value(value: str) -> str:
    if not value or value != value.strip() or "\n" in value or value.startswith(_NEEDS_QUOTING_PREFIXES):
        return json.dumps(value)
    return value
