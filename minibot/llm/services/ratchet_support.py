from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, RootModel, model_validator
from ratchet_sm import FailAction, RetryAction, State, StateMachine, ValidAction
from ratchet_sm.normalizers import HEALING_PIPELINE


class StructuredOutputValidator:
    def __init__(
        self,
        *,
        max_attempts: int = 3,
        schema: dict[str, Any] | type[BaseModel],
        state_name: str = "final_response",
    ) -> None:
        self._state_name = state_name
        machine_schema = _build_machine_schema(schema)
        self._machine = StateMachine(
            states={
                state_name: State(
                    name=state_name,
                    schema=machine_schema,
                    max_attempts=max_attempts,
                    normalizers=HEALING_PIPELINE,
                )
            },
            transitions={},
            initial=state_name,
        )

    def receive(self, payload: Any) -> ValidAction | RetryAction | FailAction:
        raw = _to_raw_text(payload)
        action = self._machine.receive(raw)
        if isinstance(action, ValidAction | RetryAction | FailAction):
            return action
        return FailAction(
            attempts=action.attempts,
            state_name=self._state_name,
            raw=raw,
            history=(),
            reason=f"Unsupported ratchet action type: {type(action).__name__}",
        )

    def reset(self) -> None:
        self._machine.reset()

    @staticmethod
    def valid_payload(action: ValidAction) -> Any:
        parsed = action.parsed
        if isinstance(parsed, RootModel):
            return parsed.root
        if isinstance(parsed, BaseModel):
            return parsed.model_dump(mode="python", exclude_none=True)
        return parsed


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


def _build_machine_schema(schema: dict[str, Any] | type[BaseModel]) -> type[BaseModel]:
    if isinstance(schema, dict):
        return _json_schema_root_model(schema)
    return schema


def _json_schema_root_model(schema: Mapping[str, Any]) -> type[RootModel[Any]]:
    schema_copy = dict(schema)

    class JsonSchemaRootModel(RootModel[Any]):
        @model_validator(mode="after")
        def _validate_against_schema(self) -> JsonSchemaRootModel:
            errors = validate_json_schema_instance(self.root, schema_copy)
            if errors:
                raise ValueError("\n".join(errors))
            return self

        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
            _ = args, kwargs
            return dict(schema_copy)

    JsonSchemaRootModel.__name__ = "JsonSchemaRootModel"
    return JsonSchemaRootModel


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


def _to_raw_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=True, default=str)
