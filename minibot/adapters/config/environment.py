from __future__ import annotations

import re
from collections.abc import Callable, Mapping

# `${NAME}` — the name class excludes ":", which is what lets `${secret:NAME}` pass through the
# environment pass untouched so the two forms can be expanded independently.
_REFERENCE = re.compile(r"(?P<escape>\$)?\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)\}")
# `${secret:NAME}` — the wider name class matches what the vault document accepts as a key.
_SECRET_REFERENCE = re.compile(r"(?P<escape>\$)?\$\{secret:(?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)\}")


def expand_environment(value: object, environment: Mapping[str, str], *, path: str = "") -> object:
    """Expand environment references in configuration values without mutating the input."""

    def resolve(name: str, where: str) -> str:
        if name not in environment:
            raise ValueError(f"environment variable {name} is not set for {where}")
        return environment[name]

    return _expand(value, _REFERENCE, resolve, path=path)


def expand_secrets(value: object, secrets: Mapping[str, str], *, path: str = "") -> object:
    """Expand ``${secret:NAME}`` references from the unlocked vault without mutating the input."""

    def resolve(name: str, where: str) -> str:
        if name not in secrets:
            raise ValueError(f"secret {name} is not in the vault for {where}")
        return secrets[name]

    return _expand(value, _SECRET_REFERENCE, resolve, path=path)


def has_secret_references(value: object) -> bool:
    """Whether any string in ``value`` carries an unescaped ``${secret:NAME}`` reference."""
    if isinstance(value, str):
        return any(not match.group("escape") for match in _SECRET_REFERENCE.finditer(value))
    if isinstance(value, dict):
        return any(has_secret_references(item) for item in value.values())
    if isinstance(value, list):
        return any(has_secret_references(item) for item in value)
    return False


def _expand(
    value: object,
    pattern: re.Pattern[str],
    resolve: Callable[[str, str], str],
    *,
    path: str,
) -> object:
    if isinstance(value, str):

        def substitute(match: re.Match[str]) -> str:
            if match.group("escape"):
                return match.group(0)[1:]
            return resolve(match.group("name"), path or "<root>")

        return pattern.sub(substitute, value)
    if isinstance(value, dict):
        return {
            key: _expand(item, pattern, resolve, path=f"{path}.{key}" if path else str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_expand(item, pattern, resolve, path=f"{path}[{index}]") for index, item in enumerate(value)]
    return value
