from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from llm_async.models.tool_call import ToolCall
from pydantic import BaseModel
from ratchet_sm import FailAction, RetryAction, State, StateMachine, ValidAction
from ratchet_sm.strategies.base import FailureContext
from ratchet_sm.strategies.validation_feedback import ValidationFeedback

from minibot.shared.parse_utils import parse_json_with_fenced_fallback


@dataclass(frozen=True)
class ToolCallMissingAction:
    attempts: int
    state_name: str
    raw: str
    prompt_patch: str | None
    errors: tuple[str, ...]
    reason: str


class ToolCallRecoveryMachine:
    def __init__(self, *, max_attempts: int, state_name: str = "tool_call") -> None:
        self._max_attempts = max_attempts
        self._state_name = state_name
        self._attempts = 0
        self._history: list[ToolCallMissingAction] = []

    def receive(self, raw: str) -> ValidAction | ToolCallMissingAction | FailAction:
        self._attempts += 1
        if self._attempts > self._max_attempts:
            return FailAction(
                attempts=self._attempts,
                state_name=self._state_name,
                raw=raw,
                history=tuple(self._history),
                reason=f"Exceeded max_attempts ({self._max_attempts})",
            )
        parsed = _extract_pseudo_tool_call(raw)
        if parsed is not None and _looks_like_tool_call_payload(parsed):
            return ValidAction(
                attempts=self._attempts,
                state_name=self._state_name,
                raw=raw,
                parsed=parsed,
                format_detected="pseudo_tool_call",
                was_cleaned=False,
            )
        reason = "pseudo_tool_call_in_text" if _has_pseudo_tool_call_tag(raw) else "no_tool_call"
        prompt_patch = (
            "Your previous response attempted a tool call in text. "
            "Do not wrap tool calls in text or tags. Use the provider's native tool call format."
            if reason == "pseudo_tool_call_in_text"
            else "If you need to use a tool, call it using the provider's native tool call format."
        )
        action = ToolCallMissingAction(
            attempts=self._attempts,
            state_name=self._state_name,
            raw=raw,
            prompt_patch=prompt_patch,
            errors=(f"No tool call found in response (reason: {reason}).",),
            reason=reason,
        )
        self._history.append(action)
        return action

    def reset(self) -> None:
        self._attempts = 0
        self._history = []


def build_tool_call_recovery_machine(*, max_attempts: int) -> ToolCallRecoveryMachine:
    return ToolCallRecoveryMachine(max_attempts=max_attempts)


def recovered_tool_call_from_payload(payload: Mapping[str, Any]) -> ToolCall:
    name = _coerce_tool_name(payload)
    arguments = _coerce_tool_arguments(payload)
    return ToolCall(
        id=f"ratchet-{uuid4().hex}",
        type="function",
        name=name,
        input=arguments,
        function={"name": name, "arguments": json.dumps(arguments, ensure_ascii=True)},
    )


class StructuredOutputValidator:
    def __init__(
        self,
        *,
        max_attempts: int = 3,
        schema: dict[str, Any] | type[BaseModel],
        state_name: str = "final_response",
    ) -> None:
        self._state_name = state_name
        self._schema = schema
        self._attempts = 0
        self._history: list[RetryAction] = []
        self._feedback = ValidationFeedback()
        self._machine: StateMachine | None = None
        if not isinstance(schema, dict):
            self._machine = StateMachine(
                states={
                    state_name: State(
                        name=state_name,
                        schema=schema,
                        max_attempts=max_attempts,
                    )
                },
                transitions={},
                initial=state_name,
            )
        self._max_attempts = max_attempts

    def receive(self, payload: Any) -> ValidAction | RetryAction | FailAction:
        if self._machine is not None:
            action = self._machine.receive(_to_raw_text(payload))
            if isinstance(action, ValidAction | RetryAction | FailAction):
                return action
            return FailAction(
                attempts=1,
                state_name=self._state_name,
                raw=_to_raw_text(payload),
                history=(),
                reason=f"Unsupported ratchet action type: {type(action).__name__}",
            )

        raw = _to_raw_text(payload)
        self._attempts += 1
        if self._attempts > self._max_attempts:
            return FailAction(
                attempts=self._attempts,
                state_name=self._state_name,
                raw=raw,
                history=tuple(self._history),
                reason=f"Exceeded max_attempts ({self._max_attempts})",
            )

        try:
            parsed = parse_json_with_fenced_fallback(raw)
        except Exception:
            retry = self._retry_action(
                raw=raw,
                errors=["Could not parse output into a structured format."],
                reason="parse_error",
            )
            self._history.append(retry)
            return retry

        errors = validate_json_schema_instance(parsed, self._schema)
        if errors:
            retry = self._retry_action(raw=raw, errors=errors, reason="validation_error")
            self._history.append(retry)
            return retry
        return ValidAction(
            attempts=self._attempts,
            state_name=self._state_name,
            raw=raw,
            parsed=parsed,
            format_detected="json",
            was_cleaned=False,
        )

    def reset(self) -> None:
        self._attempts = 0
        self._history = []
        if self._machine is not None:
            self._machine.reset()

    @staticmethod
    def valid_payload(action: ValidAction) -> Any:
        parsed = action.parsed
        if isinstance(parsed, BaseModel):
            return parsed.model_dump(mode="python", exclude_none=True)
        return parsed

    def _retry_action(self, *, raw: str, errors: list[str], reason: str) -> RetryAction:
        context = FailureContext(
            raw=raw,
            errors=errors,
            attempts=self._attempts,
            schema=self._schema if not isinstance(self._schema, dict) else None,
            schema_format="json_schema",
        )
        prompt_patch = self._feedback.on_failure(context)
        if isinstance(self._schema, dict):
            prompt_patch = _render_schema_retry_prompt(errors, self._schema)
        return RetryAction(
            attempts=self._attempts,
            state_name=self._state_name,
            raw=raw,
            prompt_patch=prompt_patch,
            errors=tuple(errors),
            reason=reason,
        )


def validate_json_schema_instance(instance: Any, schema: Mapping[str, Any], *, path: str = "$") -> list[str]:
    errors: list[str] = []
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if not any(not validate_json_schema_instance(instance, {"type": item}, path=path) for item in schema_type):
            errors.append(f"{path}: expected one of {schema_type}, got {type(instance).__name__}")
            return errors
    elif isinstance(schema_type, str):
        type_errors = _validate_type(instance, schema_type, path)
        if type_errors:
            return type_errors

    any_of = schema.get("anyOf")
    if isinstance(any_of, Sequence) and not isinstance(any_of, (str, bytes)):
        if not any(
            not validate_json_schema_instance(instance, option, path=path)
            for option in any_of
            if isinstance(option, Mapping)
        ):
            errors.append(f"{path}: value did not match any allowed schema")
            return errors

    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes)) and instance not in enum:
        errors.append(f"{path}: expected one of {list(enum)!r}, got {instance!r}")

    if isinstance(instance, Mapping):
        properties = schema.get("properties")
        required = schema.get("required")
        if isinstance(required, Sequence) and not isinstance(required, (str, bytes)):
            for key in required:
                if isinstance(key, str) and key not in instance:
                    errors.append(f"{path}: missing required property '{key}'")
        if isinstance(properties, Mapping):
            for key, subschema in properties.items():
                if key not in instance:
                    continue
                if isinstance(subschema, Mapping):
                    errors.extend(validate_json_schema_instance(instance[key], subschema, path=f"{path}.{key}"))
            additional = schema.get("additionalProperties", True)
            if additional is False:
                allowed = {str(key) for key in properties}
                for key in instance:
                    if str(key) not in allowed:
                        errors.append(f"{path}: unexpected property '{key}'")
            elif isinstance(additional, Mapping):
                for key, value in instance.items():
                    if key in properties:
                        continue
                    errors.extend(validate_json_schema_instance(value, additional, path=f"{path}.{key}"))
    elif isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(instance):
                errors.extend(validate_json_schema_instance(item, items, path=f"{path}[{index}]"))

    return errors


def _validate_type(instance: Any, schema_type: str, path: str) -> list[str]:
    if schema_type == "object" and isinstance(instance, Mapping):
        return []
    if schema_type == "array" and isinstance(instance, list):
        return []
    if schema_type == "string" and isinstance(instance, str):
        return []
    if schema_type == "boolean" and isinstance(instance, bool):
        return []
    if schema_type == "integer" and isinstance(instance, int) and not isinstance(instance, bool):
        return []
    if schema_type == "number" and isinstance(instance, (int, float)) and not isinstance(instance, bool):
        return []
    if schema_type == "null" and instance is None:
        return []
    return [f"{path}: expected {schema_type}, got {type(instance).__name__}"]


def _render_schema_retry_prompt(errors: list[str], schema: Mapping[str, Any]) -> str:
    errors_str = "\n".join(f"- {error}" for error in errors)
    schema_str = json.dumps(schema, ensure_ascii=True, indent=2, sort_keys=True)
    return (
        "Your previous response did not match the expected format.\n"
        f"Errors:\n{errors_str}\n\n"
        f"Schema:\n{schema_str}\n\n"
        "Please try again."
    )


def _to_raw_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=True, default=str)


def _coerce_tool_name(payload: Mapping[str, Any]) -> str:
    for key in ("name", "tool", "tool_name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    function = payload.get("function")
    if isinstance(function, Mapping):
        value = function.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("Recovered tool call missing name")


def _coerce_tool_arguments(payload: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("arguments", "args", "input", "parameters"):
        value = payload.get(key)
        coerced = _coerce_argument_object(value)
        if coerced is not None:
            return coerced
    function = payload.get("function")
    if isinstance(function, Mapping):
        coerced = _coerce_argument_object(function.get("arguments"))
        if coerced is not None:
            return coerced
    return {}


def _coerce_argument_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        decoded = parse_json_with_fenced_fallback(value)
        if isinstance(decoded, Mapping):
            return dict(decoded)
    return None


def _has_pseudo_tool_call_tag(raw: str) -> bool:
    markers = (
        "<tool_call>",
        "</tool_call>",
        "<function_call>",
        "</function_call>",
        "```tool_call",
        "```function_call",
        "[TOOL_CALL]",
        "[/TOOL_CALL]",
    )
    lowered = raw.lower()
    return any(marker.lower() in lowered for marker in markers)


def _extract_pseudo_tool_call(raw: str) -> dict[str, Any] | None:
    candidates = [raw.strip()]
    tag_pairs = (
        ("<tool_call>", "</tool_call>"),
        ("<function_call>", "</function_call>"),
        ("[tool_call]", "[/tool_call]"),
    )
    lowered = raw.lower()
    for start, end in tag_pairs:
        start_index = lowered.find(start)
        end_index = lowered.find(end)
        if start_index != -1 and end_index != -1 and end_index > start_index:
            body = raw[start_index + len(start) : end_index].strip()
            if body:
                candidates.append(body)
    if lowered.startswith("```tool_call") or lowered.startswith("```function_call"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidates.append("\n".join(lines[1:-1]).strip())

    for candidate in candidates:
        try:
            parsed = parse_json_with_fenced_fallback(candidate)
        except Exception:
            continue
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return None


def _looks_like_tool_call_payload(payload: Mapping[str, Any]) -> bool:
    if any(isinstance(payload.get(key), str) and payload.get(key) for key in ("name", "tool", "tool_name")):
        return True
    function = payload.get("function")
    if isinstance(function, Mapping) and isinstance(function.get("name"), str) and function.get("name"):
        return True
    return False
