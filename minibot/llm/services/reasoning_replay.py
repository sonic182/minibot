from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Raw thinking arrives beside ``content`` on a chat-completions message, under a field name that
# differs per provider: DeepSeek and its OpenAI-compatible gateways use ``reasoning_content``,
# OpenRouter-style ones use ``reasoning``. Responses providers are unaffected — their reasoning is a
# separate output item, extracted by extract_reasoning_text_from_responses.
_REASONING_TEXT_KEYS = ("reasoning", "reasoning_content")


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

    for key in _REASONING_TEXT_KEYS:
        reasoning = _coerce_reasoning(getattr(message, key, None))
        if reasoning:
            return ReasoningReplay(
                reasoning=reasoning,
                reasoning_details=None,
                original_had_reasoning=True,
                source=f"message.{key}",
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
        for key in _REASONING_TEXT_KEYS:
            original_reasoning = _coerce_reasoning(original.get(key))
            if original_reasoning:
                return ReasoningReplay(
                    reasoning=original_reasoning,
                    reasoning_details=None,
                    original_had_reasoning=True,
                    source=f"message.original.{key}",
                )

    return ReasoningReplay(
        reasoning=None,
        reasoning_details=None,
        original_had_reasoning=original_had_reasoning,
        source=None,
    )


def reasoning_text_from_message(message: Any) -> str | None:
    """Flatten whatever reasoning a provider message carries into displayable text."""
    replay = extract_reasoning_replay(message)
    if replay.reasoning:
        return replay.reasoning
    parts: list[str] = []
    for item in replay.reasoning_details or []:
        parts.extend(_collect_reasoning_texts(item))
    return "\n\n".join(parts) or None


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
    return any(_coerce_reasoning(value.get(key)) is not None for key in _REASONING_TEXT_KEYS)


def extract_reasoning_text_from_responses(payload: Mapping[str, Any]) -> str | None:
    """Extract model reasoning from a raw OpenAI Responses API payload.

    Reasoning arrives as ``output`` items of type ``reasoning``. Prefer their
    concise ``summary``, fall back to full ``content``, then to a raw scan of
    any ``text``/``content``/``summary_text`` strings.
    """
    parts: list[str] = []

    top = _coerce_reasoning(payload.get("reasoning"))
    if top:
        parts.append(top)

    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, Mapping) or item.get("type") != "reasoning":
                continue
            texts = _collect_reasoning_texts(item.get("summary"))
            if not texts:
                texts = _collect_reasoning_texts(item.get("content"))
            if not texts:
                texts = _collect_reasoning_texts(item)
            parts.extend(texts)

    return "\n\n".join(parts) or None


_TEXT_KEYS = {"text", "content", "summary", "summary_text"}


def _collect_reasoning_texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Mapping):
        texts: list[str] = []
        for key, child in value.items():
            if key in _TEXT_KEYS and isinstance(child, str):
                if child.strip():
                    texts.append(child.strip())
            elif isinstance(child, (Mapping, list, tuple)):
                texts.extend(_collect_reasoning_texts(child))
        return texts
    if isinstance(value, (list, tuple)):
        texts: list[str] = []
        for child in value:
            texts.extend(_collect_reasoning_texts(child))
        return texts
    return []
