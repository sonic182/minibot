from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from ratchet_sm import FailAction, RetryAction, ValidAction

from minibot.llm.services.ratchet_support import StructuredOutputValidator


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
    should_answer_to_user: bool = True
    continue_loop: bool = False
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("attachments", mode="before")
    @classmethod
    def _normalize_attachments(cls, value: Any) -> Any:
        if value is None:
            return []
        return value


class RuntimeStructuredOutputValidator:
    def __init__(
        self,
        *,
        max_attempts: int = 3,
        schema_model: dict[str, Any] | type[BaseModel] = AssistantRuntimePayload,
    ) -> None:
        self._validator = StructuredOutputValidator(max_attempts=max_attempts, schema=schema_model)

    def receive(self, payload: Any) -> ValidAction | RetryAction | FailAction:
        return self._validator.receive(payload)

    def valid_payload(self, action: ValidAction) -> dict[str, Any]:
        payload = self._validator.valid_payload(action)
        if not isinstance(payload, dict):
            raise TypeError(f"Expected a dict payload, got {type(payload).__name__}")
        return payload

    def reset(self) -> None:
        self._validator.reset()

    @staticmethod
    def fallback_payload() -> dict[str, Any]:
        return {
            "answer": {
                "kind": "text",
                "content": "I could not produce a valid structured response in this attempt. Please try again.",
            },
            "should_answer_to_user": True,
        }
