from __future__ import annotations

from minibot.app.tool_guardrail_validator import ToolGuardrailValidator, _FailResult, _RetryResult, _ValidResult


def test_tool_guardrail_validator_accepts_valid_payload() -> None:
    validator = ToolGuardrailValidator(max_attempts=2)

    result = validator.receive(
        {"requires_tools": True, "suggested_tool": "filesystem", "path": "tmp/a.txt", "reason": "delete request"}
    )

    assert isinstance(result, _ValidResult)
    payload = validator.valid_payload(result)
    assert payload.requires_tools is True
    assert payload.suggested_tool == "filesystem"
    assert payload.path == "tmp/a.txt"
    assert payload.reason == "delete request"


def test_tool_guardrail_validator_retries_on_invalid_then_fails() -> None:
    validator = ToolGuardrailValidator(max_attempts=1)

    first = validator.receive("not json")
    second = validator.receive("still not json")

    assert isinstance(first, _RetryResult)
    assert isinstance(second, _FailResult)
    assert "invalid JSON" in second.reason
