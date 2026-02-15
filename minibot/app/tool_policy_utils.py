from __future__ import annotations

from fnmatch import fnmatch
from typing import Callable, Iterable, Sequence, TypeVar


_T = TypeVar("_T")


def normalize_patterns(patterns: Iterable[str]) -> list[str]:
    return [item.strip() for item in patterns if item.strip()]


def matches_any(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch(name, pattern) for pattern in patterns)


def validate_allow_deny(allow_patterns: Sequence[str], deny_patterns: Sequence[str]) -> None:
    if allow_patterns and deny_patterns:
        raise ValueError("only one of tools_allow or tools_deny can be set")


def apply_allow_deny(
    items: Sequence[_T],
    *,
    name_of: Callable[[_T], str],
    allow_patterns: Sequence[str],
    deny_patterns: Sequence[str],
) -> list[_T]:
    if allow_patterns and deny_patterns:
        raise ValueError("only one of allow_patterns or deny_patterns can be set")
    if allow_patterns:
        return [item for item in items if matches_any(name_of(item), allow_patterns)]
    if deny_patterns:
        return [item for item in items if not matches_any(name_of(item), deny_patterns)]
    return list(items)
