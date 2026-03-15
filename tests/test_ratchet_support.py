from __future__ import annotations

from pydantic import BaseModel
from ratchet_sm import FailAction, RetryAction, ValidAction

from minibot.llm.services.ratchet_support import StructuredOutputValidator


class _PayloadModel(BaseModel):
    answer: str


def test_structured_output_validator_heals_malformed_json_dict_schema() -> None:
    validator = StructuredOutputValidator(
        max_attempts=3,
        schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )

    action = validator.receive('{"answer":"ok",}')

    assert isinstance(action, ValidAction)
    assert action.format_detected == "repair_json"
    assert validator.valid_payload(action) == {"answer": "ok"}


def test_structured_output_validator_retries_and_reset_restores_attempt_budget() -> None:
    validator = StructuredOutputValidator(max_attempts=2, schema=_PayloadModel)

    first = validator.receive("not-json")
    second = validator.receive("still not-json")
    third = validator.receive("still not-json")

    assert isinstance(first, RetryAction)
    assert isinstance(second, RetryAction)
    assert isinstance(third, FailAction)

    validator.reset()
    recovered = validator.receive('{"answer":"ok"}')

    assert isinstance(recovered, ValidAction)
    assert validator.valid_payload(recovered) == {"answer": "ok"}
