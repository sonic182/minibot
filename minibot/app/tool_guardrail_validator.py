from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator
from ratchet_sm import FailAction, RetryAction, State, StateMachine, ValidAction
from ratchet_sm.normalizers import ParseJSON, StripFences


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


class ToolGuardrailValidator:
    def __init__(self, *, max_attempts: int = 3) -> None:
        self._machine = StateMachine(
            states={
                "tool_guardrail": State(
                    name="tool_guardrail",
                    schema=ToolGuardrailPayload,
                    max_attempts=max_attempts,
                    normalizers=[StripFences(), ParseJSON()],
                )
            },
            transitions={},
            initial="tool_guardrail",
        )

    def receive(self, payload: Any) -> ValidAction | RetryAction | FailAction:
        raw = _to_raw_text(payload)
        action = self._machine.receive(raw)
        if isinstance(action, ValidAction | RetryAction | FailAction):
            return action
        return FailAction(
            attempts=1,
            state_name="tool_guardrail",
            raw=raw,
            history=(),
            reason=f"Unsupported ratchet action type: {type(action).__name__}",
        )

    def valid_payload(self, action: ValidAction) -> ToolGuardrailPayload:
        parsed = action.parsed
        if not isinstance(parsed, ToolGuardrailPayload):
            raise TypeError(f"Expected ToolGuardrailPayload, got {type(parsed).__name__}")
        return parsed


def _to_raw_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=True, default=str)
