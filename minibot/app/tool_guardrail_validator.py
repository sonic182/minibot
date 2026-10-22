from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, cast, overload

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


class ToolGuardrailPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requires_tools: bool
    suggested_tool: str | None = None
    path: str | None = None
    reason: str | None = None

    @field_validator("suggested_tool", "path", "reason", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return str(value)


@dataclass(frozen=True)
class ValidResult[PayloadT: BaseModel]:
    parsed: PayloadT
    attempts: int


@dataclass(frozen=True)
class RetryResult:
    attempts: int
    reason: str
    prompt_patch: str


@dataclass(frozen=True)
class FailResult:
    attempts: int
    reason: str


class ToolGuardrailValidator[PayloadT: BaseModel]:
    """Parses a classifier's JSON reply into ``payload_model`` (default: ``ToolGuardrailPayload``), asking for a
    bounded number of retries."""

    @overload
    def __init__(self: ToolGuardrailValidator[ToolGuardrailPayload], *, max_attempts: int = 3) -> None: ...

    @overload
    def __init__(self, *, max_attempts: int = 3, payload_model: type[PayloadT]) -> None: ...

    def __init__(self, *, max_attempts: int = 3, payload_model: type[BaseModel] = ToolGuardrailPayload) -> None:
        self._max_attempts = max(1, max_attempts)
        self._payload_model = cast(type[PayloadT], payload_model)
        self._attempts = 0

    def receive(self, payload: Any) -> ValidResult[PayloadT] | RetryResult | FailResult:
        self._attempts += 1
        raw = _to_raw_text(payload)
        text = _strip_fences(raw)
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            reason = f"invalid JSON: {exc}"
            if self._attempts <= self._max_attempts:
                return RetryResult(
                    attempts=self._attempts,
                    reason=reason,
                    prompt_patch=f"Your previous response was not valid JSON. Error: {exc}. Return only a JSON object.",  # noqa: E501
                )
            return FailResult(attempts=self._attempts, reason=reason)
        try:
            parsed = self._payload_model.model_validate(data)
        except ValidationError as exc:
            reason = f"schema validation failed: {exc}"
            if self._attempts <= self._max_attempts:
                return RetryResult(
                    attempts=self._attempts,
                    reason=reason,
                    prompt_patch=f"Your previous response did not match the expected schema. {exc}",
                )
            return FailResult(attempts=self._attempts, reason=reason)
        return ValidResult(parsed=parsed, attempts=self._attempts)

    def valid_payload(self, result: ValidResult[PayloadT]) -> PayloadT:
        return result.parsed


def _strip_fences(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _to_raw_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=True, default=str)
