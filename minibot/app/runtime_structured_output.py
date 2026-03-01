from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from ratchet_sm import FailAction, RetryAction, State, StateMachine, ValidAction
from ratchet_sm.normalizers import ParseJSON, StripFences


class AssistantAnswerMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disable_link_preview: bool | None = None


class AssistantAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "html", "markdown"]
    content: str
    meta: AssistantAnswerMeta = Field(default_factory=AssistantAnswerMeta)

    @field_validator("meta", mode="before")
    @classmethod
    def _normalize_meta(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value


class AssistantRuntimePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: AssistantAnswer
    should_answer_to_user: bool
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("attachments", mode="before")
    @classmethod
    def _normalize_attachments(cls, value: Any) -> Any:
        if value is None:
            return []
        return value


class RuntimeStructuredOutputValidator:
    def __init__(self, *, max_attempts: int = 3) -> None:
        self._machine = StateMachine(
            states={
                "final_response": State(
                    name="final_response",
                    schema=AssistantRuntimePayload,
                    max_attempts=max_attempts,
                    normalizers=[StripFences(), ParseJSON()],
                )
            },
            transitions={},
            initial="final_response",
        )

    def receive(self, payload: Any) -> ValidAction | RetryAction | FailAction:
        action = self._machine.receive(_to_raw_text(payload))
        if isinstance(action, ValidAction | RetryAction | FailAction):
            return action
        return FailAction(
            attempts=1,
            state_name="final_response",
            raw=_to_raw_text(payload),
            history=(),
            reason=f"Unsupported ratchet action type: {type(action).__name__}",
        )

    @staticmethod
    def valid_payload(action: ValidAction) -> dict[str, Any]:
        parsed = action.parsed
        if isinstance(parsed, AssistantRuntimePayload):
            return parsed.model_dump(mode="python", exclude_none=True)
        if isinstance(parsed, dict):
            return parsed
        return AssistantRuntimePayload.model_validate(parsed).model_dump(mode="python", exclude_none=True)

    @staticmethod
    def fallback_payload() -> dict[str, Any]:
        return {
            "answer": {
                "kind": "text",
                "content": "I could not produce a valid structured response in this attempt. Please try again.",
            },
            "should_answer_to_user": True,
        }


def _to_raw_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=True, default=str)
