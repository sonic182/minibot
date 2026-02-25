from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minibot.llm.services.models import LLMGeneration, UsageSnapshot
from minibot.shared.parse_utils import parse_json_with_fenced_fallback


def parse_structured_payload(payload: str) -> Any:
    return parse_json_with_fenced_fallback(payload)


def extract_response_id(response: Any) -> str | None:
    original = getattr(response, "original", None)
    if isinstance(original, dict):
        resp_id = original.get("id")
        if isinstance(resp_id, str):
            return resp_id
    return None


def extract_total_tokens(response: Any) -> int | None:
    original = getattr(response, "original", None)
    if not isinstance(original, dict):
        return None
    return extract_total_tokens_from_payload(original)


def extract_usage_from_response(response: Any) -> UsageSnapshot:
    original = getattr(response, "original", None)
    if not isinstance(original, dict):
        return UsageSnapshot()
    return extract_usage_from_payload(original)


def extract_total_tokens_from_payload(original: dict[str, Any]) -> int | None:
    return extract_usage_from_payload(original).total_tokens


def extract_usage_from_payload(original: dict[str, Any]) -> UsageSnapshot:
    usage = original.get("usage")
    if not isinstance(usage, dict):
        return UsageSnapshot(
            status=opt_str(original.get("status")),
            incomplete_reason=incomplete_reason(original),
        )
    total_tokens = opt_int(usage.get("total_tokens"))
    input_tokens = opt_int(usage.get("input_tokens"))
    output_tokens = opt_int(usage.get("output_tokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if total_tokens is None:
        prompt_tokens = opt_int(usage.get("prompt_tokens"))
        completion_tokens = opt_int(usage.get("completion_tokens"))
        if prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens
        if input_tokens is None:
            input_tokens = prompt_tokens
        if output_tokens is None:
            output_tokens = completion_tokens

    cached_input_tokens = None
    input_details = usage.get("input_tokens_details")
    if isinstance(input_details, dict):
        cached_input_tokens = opt_int(input_details.get("cached_tokens"))

    reasoning_output_tokens = None
    output_details = usage.get("output_tokens_details")
    if isinstance(output_details, dict):
        reasoning_output_tokens = opt_int(output_details.get("reasoning_tokens"))

    return UsageSnapshot(
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_output_tokens=reasoning_output_tokens,
        status=opt_str(original.get("status")),
        incomplete_reason=incomplete_reason(original),
    )


def should_auto_continue_incomplete(usage: UsageSnapshot) -> bool:
    if usage.status != "incomplete":
        return False
    reason = (usage.incomplete_reason or "").strip().lower()
    if not reason:
        return False
    return reason in {"max_output_tokens", "max_tokens"} or "max_output" in reason


@dataclass
class UsageAccumulator:
    total_tokens_used: int = 0
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    cached_input_tokens_used: int = 0
    reasoning_output_tokens_used: int = 0
    saw_input_tokens: bool = False
    saw_output_tokens: bool = False
    saw_cached_input_tokens: bool = False
    saw_reasoning_output_tokens: bool = False

    def add_step(self, usage: UsageSnapshot, usage_tokens: int | None) -> None:
        if usage_tokens is not None:
            self.total_tokens_used += usage_tokens
        if usage.input_tokens is not None:
            self.input_tokens_used += usage.input_tokens
            self.saw_input_tokens = True
        if usage.output_tokens is not None:
            self.output_tokens_used += usage.output_tokens
            self.saw_output_tokens = True
        if usage.cached_input_tokens is not None:
            self.cached_input_tokens_used += usage.cached_input_tokens
            self.saw_cached_input_tokens = True
        if usage.reasoning_output_tokens is not None:
            self.reasoning_output_tokens_used += usage.reasoning_output_tokens
            self.saw_reasoning_output_tokens = True

    def add_generation(self, generation: LLMGeneration) -> None:
        if generation.total_tokens is not None:
            self.total_tokens_used += generation.total_tokens
        if generation.input_tokens is not None:
            self.input_tokens_used += generation.input_tokens
            self.saw_input_tokens = True
        if generation.output_tokens is not None:
            self.output_tokens_used += generation.output_tokens
            self.saw_output_tokens = True
        if generation.cached_input_tokens is not None:
            self.cached_input_tokens_used += generation.cached_input_tokens
            self.saw_cached_input_tokens = True
        if generation.reasoning_output_tokens is not None:
            self.reasoning_output_tokens_used += generation.reasoning_output_tokens
            self.saw_reasoning_output_tokens = True

    def build_generation(
        self,
        *,
        payload: Any,
        response_id: str | None,
        status: str | None,
        incomplete_reason: str | None,
    ) -> LLMGeneration:
        return LLMGeneration(
            payload,
            response_id,
            total_tokens=self.total_tokens_used or None,
            input_tokens=self.input_tokens_used if self.saw_input_tokens else None,
            output_tokens=self.output_tokens_used if self.saw_output_tokens else None,
            cached_input_tokens=self.cached_input_tokens_used if self.saw_cached_input_tokens else None,
            reasoning_output_tokens=self.reasoning_output_tokens_used if self.saw_reasoning_output_tokens else None,
            status=status,
            incomplete_reason=incomplete_reason,
        )


def opt_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def opt_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def incomplete_reason(original: dict[str, Any]) -> str | None:
    details = original.get("incomplete_details")
    if isinstance(details, dict):
        reason = details.get("reason")
        if isinstance(reason, str) and reason:
            return reason
    return None
