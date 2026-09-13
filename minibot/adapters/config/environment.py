from __future__ import annotations

import re
from collections.abc import Mapping

_REFERENCE = re.compile(r"(?P<escape>\$)?\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")


def expand_environment(value: object, environment: Mapping[str, str], *, path: str = "") -> object:
    """Expand environment references in configuration values without mutating the input."""
    if isinstance(value, str):

        def substitute(match: re.Match[str]) -> str:
            if match.group("escape"):
                return match.group(0)[1:]
            name = match.group("name")
            if name not in environment:
                raise ValueError(f"environment variable {name} is not set for {path or '<root>'}")
            return environment[name]

        return _REFERENCE.sub(substitute, value)
    if isinstance(value, dict):
        return {
            key: expand_environment(item, environment, path=f"{path}.{key}" if path else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [expand_environment(item, environment, path=f"{path}[{index}]") for index, item in enumerate(value)]
    return value
