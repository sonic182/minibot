from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReasoningReplay:
    reasoning: str | None
    reasoning_details: list[dict[str, Any] | str] | None
    original_had_reasoning: bool
    source: str | None

    @property
    def has_replayable_reasoning(self) -> bool:
        return bool(self.reasoning_details) or bool(self.reasoning)


def extract_reasoning_replay(message: Any) -> ReasoningReplay:
    reasoning_details = _coerce_reasoning_details(getattr(message, "reasoning_details", None))
    if reasoning_details:
        return ReasoningReplay(
            reasoning=None,
            reasoning_details=reasoning_details,
            original_had_reasoning=True,
            source="message.reasoning_details",
        )

    reasoning = _coerce_reasoning(getattr(message, "reasoning", None))
    if reasoning:
        return ReasoningReplay(
            reasoning=reasoning,
            reasoning_details=None,
            original_had_reasoning=True,
            source="message.reasoning",
        )

    original = getattr(message, "original", None)
    original_had_reasoning = _message_like_has_reasoning(original)
    if isinstance(original, Mapping):
        original_reasoning_details = _coerce_reasoning_details(original.get("reasoning_details"))
        if original_reasoning_details:
            return ReasoningReplay(
                reasoning=None,
                reasoning_details=original_reasoning_details,
                original_had_reasoning=True,
                source="message.original.reasoning_details",
            )
        original_reasoning = _coerce_reasoning(original.get("reasoning"))
        if original_reasoning:
            return ReasoningReplay(
                reasoning=original_reasoning,
                reasoning_details=None,
                original_had_reasoning=True,
                source="message.original.reasoning",
            )

    return ReasoningReplay(
        reasoning=None,
        reasoning_details=None,
        original_had_reasoning=original_had_reasoning,
        source=None,
    )


def apply_reasoning_replay(payload: dict[str, Any], replay: ReasoningReplay) -> dict[str, Any]:
    updated = dict(payload)
    if replay.reasoning_details:
        updated["reasoning_details"] = [
            dict(item) if isinstance(item, Mapping) else item for item in replay.reasoning_details
        ]
        updated.pop("reasoning", None)
    elif replay.reasoning:
        updated["reasoning"] = replay.reasoning
        updated.pop("reasoning_details", None)
    if replay.original_had_reasoning and "reasoning" not in updated and "reasoning_details" not in updated:
        raise RuntimeError("provider reasoning context would be dropped during follow-up replay")
    return updated


def _coerce_reasoning(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _coerce_reasoning_details(value: Any) -> list[dict[str, Any] | str] | None:
    if not isinstance(value, list):
        return None
    details: list[dict[str, Any] | str] = []
    for item in value:
        if isinstance(item, Mapping):
            details.append(dict(item))
        elif isinstance(item, str):
            details.append(item)
    return details or None


def _message_like_has_reasoning(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    if _coerce_reasoning_details(value.get("reasoning_details")):
        return True
    return _coerce_reasoning(value.get("reasoning")) is not None
