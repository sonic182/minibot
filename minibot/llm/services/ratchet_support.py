from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError
from ratchet_sm import FailAction, RetryAction, ValidAction
from ratchet_sm.strategies.base import FailureContext
from ratchet_sm.strategies.validation_feedback import ValidationFeedback

from minibot.shared.parse_utils import parse_json_with_fenced_fallback


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
        self._max_attempts = max_attempts

    def receive(self, payload: Any) -> ValidAction | RetryAction | FailAction:
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

        if isinstance(self._schema, dict):
            errors = validate_json_schema_instance(parsed, self._schema)
            if errors:
                retry = self._retry_action(raw=raw, errors=errors, reason="validation_error")
                self._history.append(retry)
                return retry
        else:
            try:
                parsed = self._schema.model_validate(parsed)
            except ValidationError as exc:
                retry = self._retry_action(
                    raw=raw,
                    errors=_format_pydantic_errors(exc),
                    reason="validation_error",
                )
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
            schema_format="pydantic" if not isinstance(self._schema, dict) else "json_schema",
        )
        prompt_patch = self._feedback.on_failure(context)
        if isinstance(self._schema, dict):
            prompt_patch = _render_schema_retry_prompt(errors, self._schema)
        else:
            prompt_patch = _render_pydantic_retry_prompt(errors, self._schema)
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


def _render_pydantic_retry_prompt(errors: list[str], model: type[BaseModel]) -> str:
    errors_str = "\n".join(f"- {error}" for error in errors)
    schema_str = json.dumps(model.model_json_schema(), ensure_ascii=True, indent=2, sort_keys=True)
    return (
        "Your previous response did not match the expected Pydantic model.\n"
        f"Pydantic validation errors:\n{errors_str}\n\n"
        f"Model JSON schema:\n{schema_str}\n\n"
        "Please return a corrected JSON object that satisfies the model exactly."
    )


def _format_pydantic_errors(exc: ValidationError) -> list[str]:
    formatted: list[str] = []
    for error in exc.errors():
        location = error.get("loc", ())
        if isinstance(location, Sequence) and not isinstance(location, (str, bytes)):
            path = ".".join(str(item) for item in location) or "$"
        else:
            path = str(location) or "$"
        formatted.append(f"{path}: {error.get('msg', 'validation error')}")
    return formatted


def _to_raw_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=True, default=str)

