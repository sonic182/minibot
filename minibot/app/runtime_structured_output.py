from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from ratchet_sm import FailAction, RetryAction, ValidAction

from minibot.llm.services.ratchet_support import StructuredOutputValidator
from minibot.shared.assistant_response import AssistantRuntimePayload


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
            "should_continue": False,
            "attachments": [],
        }
