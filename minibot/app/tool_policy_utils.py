from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from fnmatch import fnmatch
from typing import TypeVar

ItemT = TypeVar("ItemT")


def normalize_patterns(patterns: Iterable[str]) -> list[str]:
    return [item.strip() for item in patterns if item.strip()]


def matches_any(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch(name, pattern) for pattern in patterns)


def validate_allow_deny(allow_patterns: Sequence[str], deny_patterns: Sequence[str]) -> None:
    if allow_patterns and deny_patterns:
        raise ValueError("only one of tools_allow or tools_deny can be set")


def apply_allow_deny(  # noqa: UP047
    items: Sequence[ItemT],
    *,
    name_of: Callable[[ItemT], str],
    allow_patterns: Sequence[str],
    deny_patterns: Sequence[str],
) -> list[ItemT]:
    if allow_patterns and deny_patterns:
        raise ValueError("only one of allow_patterns or deny_patterns can be set")
    if allow_patterns:
        return [item for item in items if matches_any(name_of(item), allow_patterns)]
    if deny_patterns:
        return [item for item in items if not matches_any(name_of(item), deny_patterns)]
    return list(items)
