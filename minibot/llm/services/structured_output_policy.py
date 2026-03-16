from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal


StructuredOutputMode = Literal["provider_with_fallback", "prompt_only", "provider_strict"]

_DEFAULT_MODE: StructuredOutputMode = "provider_with_fallback"


def normalize_structured_output_mode(value: str | None) -> StructuredOutputMode:
    if value in {"provider_with_fallback", "prompt_only", "provider_strict"}:
        return value
    return _DEFAULT_MODE


def should_send_response_schema(mode: StructuredOutputMode) -> bool:
    return mode != "prompt_only"


def should_retry_without_response_schema(
    *,
    call_kwargs: dict[str, Any],
    exc: Exception,
    mode: StructuredOutputMode,
) -> bool:
    if mode != "provider_with_fallback":
        return False
    if call_kwargs.get("response_schema") is None:
        return False
    if isinstance(exc, NotImplementedError):
        return True
    message = str(exc).lower()
    if "does not support structured outputs" in message:
        return True
    if "json mode is not supported" in message:
        return True
    if '"code":20024' in message:
        return True
    if "invalid schema for response_format" in message:
        return True
    if "'allof' is not permitted" in message:
        return True
    if "response_schema" in message and ("unsupported" in message or "not supported" in message):
        return True
    if "response_format" in message and ("unsupported" in message or "not supported" in message):
        return True
    return False


def apply_structured_output_prompt(call_kwargs: dict[str, Any], response_schema: Any) -> dict[str, Any]:
    if not isinstance(response_schema, Mapping):
        return dict(call_kwargs)
    prompt = build_structured_output_instruction(response_schema)
    updated = dict(call_kwargs)
    instructions = updated.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        updated["instructions"] = f"{instructions.rstrip()}\n\n{prompt}"
    messages = updated.get("messages")
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        updated["messages"] = append_prompt_to_messages(messages=messages, prompt=prompt)
    return updated


def augment_system_prompt_with_structured_output(system_prompt: str, response_schema: Any) -> str:
    if not isinstance(response_schema, Mapping):
        return system_prompt
    prompt = build_structured_output_instruction(response_schema)
    return f"{system_prompt.rstrip()}\n\n{prompt}"


def append_prompt_to_messages(*, messages: Sequence[Any], prompt: str) -> list[Any]:
    updated_messages: list[Any] = []
    inserted = False
    for message in messages:
        if (
            not inserted
            and isinstance(message, Mapping)
            and message.get("role") == "system"
            and isinstance(message.get("content"), str)
        ):
            updated_message = dict(message)
            updated_message["content"] = f"{message['content'].rstrip()}\n\n{prompt}"
            updated_messages.append(updated_message)
            inserted = True
            continue
        updated_messages.append(message)
    if not inserted:
        updated_messages.insert(0, {"role": "system", "content": prompt})
    return updated_messages


def build_structured_output_instruction(schema: Mapping[str, Any]) -> str:
    lines = [
        "Return only a JSON object. Do not wrap it in markdown fences. Do not add any prose before or after it.",
        "",
        "Expected shape:",
    ]
    lines.extend(_describe_schema_node(node=schema, key_name=None, depth=0))
    return "\n".join(lines)


def _describe_schema_node(*, node: Mapping[str, Any], key_name: str | None, depth: int) -> list[str]:
    prefix = "  " * depth
    schema_type = node.get("type")
    properties = node.get("properties")
    additional = node.get("additionalProperties", True)
    required = _required_keys(node)
    enum_values = _enum_values(node)
    title = "top-level object" if key_name is None else f"`{key_name}`"

    if schema_type == "object" or isinstance(properties, Mapping):
        line = f"{prefix}- {title}: object"
        if required:
            line = f"{line}; required keys: {', '.join(f'`{item}`' for item in required)}"
        if additional is False:
            line = f"{line}; no extra keys"
        lines = [line]
        if isinstance(properties, Mapping):
            for child_key, child_schema in properties.items():
                if isinstance(child_schema, Mapping):
                    lines.extend(_describe_schema_node(node=child_schema, key_name=str(child_key), depth=depth + 1))
        return lines

    if schema_type == "array":
        items = node.get("items")
        line = f"{prefix}- {title}: array"
        if isinstance(items, Mapping):
            item_type = _node_type(items)
            line = f"{line} of {item_type}"
        return [line]

    line = f"{prefix}- {title}: {_node_type(node)}"
    if enum_values:
        line = f"{line}; one of {', '.join(enum_values)}"
    return [line]


def _required_keys(node: Mapping[str, Any]) -> list[str]:
    required = node.get("required")
    if not isinstance(required, Sequence) or isinstance(required, (str, bytes)):
        return []
    return [str(item) for item in required if isinstance(item, str)]


def _enum_values(node: Mapping[str, Any]) -> list[str]:
    enum = node.get("enum")
    if not isinstance(enum, Sequence) or isinstance(enum, (str, bytes)):
        return []
    return [repr(item) for item in enum]


def _node_type(node: Mapping[str, Any]) -> str:
    schema_type = node.get("type")
    if isinstance(schema_type, str):
        return schema_type
    if isinstance(schema_type, Sequence) and not isinstance(schema_type, (str, bytes)):
        values = [str(item) for item in schema_type]
        return " or ".join(values)
    if isinstance(node.get("properties"), Mapping):
        return "object"
    return "value"
