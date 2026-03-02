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

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must be a non-empty string")
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
    def __init__(self, *, max_attempts: int = 3, schema_model: type[BaseModel] = AssistantRuntimePayload) -> None:
        self._schema_model = schema_model
        self._machine = StateMachine(
            states={
                "final_response": State(
                    name="final_response",
                    schema=schema_model,
                    max_attempts=max_attempts,
                    normalizers=[StripFences(), ParseJSON()],
                )
            },
            transitions={},
            initial="final_response",
        )

    def receive(self, payload: Any) -> ValidAction | RetryAction | FailAction:
        raw = _to_raw_text(payload)
        action = self._machine.receive(raw)
        if isinstance(action, ValidAction | RetryAction | FailAction):
            return action
        return FailAction(
            attempts=1,
            state_name="final_response",
            raw=raw,
            history=(),
            reason=f"Unsupported ratchet action type: {type(action).__name__}",
        )

    def valid_payload(self, action: ValidAction) -> dict[str, Any]:
        parsed = action.parsed
        if not isinstance(parsed, BaseModel):
            raise TypeError(f"Expected a Pydantic BaseModel, got {type(parsed).__name__}")
        return parsed.model_dump(mode="python", exclude_none=True)

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
