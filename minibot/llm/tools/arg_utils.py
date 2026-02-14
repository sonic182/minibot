from __future__ import annotations

from enum import Enum
from typing import Any
from typing import TypeVar

from minibot.llm.tools.base import ToolContext

_EnumT = TypeVar("_EnumT", bound=Enum)


def require_owner(context: ToolContext) -> str:
    if not context.owner_id:
        raise ValueError("owner context is required")
    return context.owner_id


def require_channel(context: ToolContext, *, message: str = "channel context is required") -> str:
    if not context.channel:
        raise ValueError(message)
    return context.channel


def require_non_empty_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def optional_str(value: Any, *, error_message: str = "Expected string value") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(error_message)
    stripped = value.strip()
    return stripped or None


def optional_bool(
    value: Any,
    *,
    default: bool,
    error_message: str,
    true_values: tuple[str, ...] = ("true", "1", "yes", "on"),
    false_values: tuple[str, ...] = ("false", "0", "no", "off"),
) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in true_values:
            return True
        if lowered in false_values:
            return False
    raise ValueError(error_message)


def optional_int(
    value: Any,
    *,
    field: str,
    min_value: int | None = None,
    allow_float: bool = False,
    allow_string: bool = True,
    reject_bool: bool = True,
    type_error: str | None = None,
    min_error: str | None = None,
) -> int | None:
    resolved_type_error = type_error or f"{field} must be an integer"
    if value is None:
        return None
    if reject_bool and isinstance(value, bool):
        raise ValueError(resolved_type_error)

    parsed: int
    if isinstance(value, int):
        parsed = value
    elif allow_float and isinstance(value, float):
        parsed = int(value)
    elif allow_string and isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        parsed = int(stripped)
    else:
        raise ValueError(resolved_type_error)

    if min_value is not None and parsed < min_value:
        resolved_min_error = min_error or f"{field} must be >= {min_value}"
        raise ValueError(resolved_min_error)
    return parsed


def int_with_default(
    value: Any,
    *,
    default: int,
    field: str,
    min_value: int | None = None,
    max_value: int | None = None,
    clamp_max: bool = False,
    allow_string: bool = True,
    reject_bool: bool = True,
    type_error: str | None = None,
    min_error: str | None = None,
    max_error: str | None = None,
) -> int:
    resolved_type_error = type_error or f"{field} must be an integer"
    if value is None:
        parsed = default
    elif reject_bool and isinstance(value, bool):
        raise ValueError(resolved_type_error)
    elif isinstance(value, int):
        parsed = value
    elif allow_string and isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            parsed = default
        else:
            parsed = int(stripped)
    else:
        raise ValueError(resolved_type_error)

    if min_value is not None and parsed < min_value:
        resolved_min_error = min_error or f"{field} must be >= {min_value}"
        raise ValueError(resolved_min_error)
    if max_value is not None and parsed > max_value:
        if clamp_max:
            return max_value
        resolved_max_error = max_error or f"{field} must be <= {max_value}"
        raise ValueError(resolved_max_error)
    return parsed


def enum_by_value(
    value: Any,
    *,
    enum_type: type[_EnumT],
    field: str,
    default: _EnumT | None = None,
    allow_falsy_default: bool = True,
) -> _EnumT:
    if value is None or (allow_falsy_default and not value):
        if default is not None:
            return default
        raise ValueError(f"invalid {field}")
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        for candidate in enum_type:
            if str(candidate.value).lower() == normalized:
                return candidate
    raise ValueError(f"invalid {field}")
