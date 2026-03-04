from __future__ import annotations

from ratchet_sm import FailAction, RetryAction, ValidAction

from minibot.app.tool_guardrail_validator import ToolGuardrailValidator


def test_tool_guardrail_validator_accepts_valid_payload() -> None:
    validator = ToolGuardrailValidator(max_attempts=2)

    action = validator.receive(
        {"requires_tools": True, "suggested_tool": "filesystem", "path": "tmp/a.txt", "reason": "delete request"}
    )

    assert isinstance(action, ValidAction)
    payload = validator.valid_payload(action)
    assert payload.requires_tools is True
    assert payload.suggested_tool == "filesystem"
    assert payload.path == "tmp/a.txt"
    assert payload.reason == "delete request"


def test_tool_guardrail_validator_retries_then_fails() -> None:
    validator = ToolGuardrailValidator(max_attempts=2)

    first = validator.receive("not json")
    second = validator.receive("still not json")
    third = validator.receive("still not json")

    assert isinstance(first, RetryAction)
    assert isinstance(second, RetryAction)
    assert isinstance(third, FailAction)
    assert third.reason.startswith("Exceeded max_attempts")
