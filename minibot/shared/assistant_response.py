from __future__ import annotations

from typing import Any, Sequence

from minibot.llm.tools.schema_utils import attachment_array_schema
from minibot.shared.parse_utils import parse_json_maybe_python_object


def assistant_response_schema(
    *,
    kinds: Sequence[str],
    include_meta: bool = False,
    include_attachments: bool = False,
) -> dict[str, Any]:
    answer_properties: dict[str, Any] = {
        "kind": {"type": "string", "enum": list(kinds)},
        "content": {"type": "string"},
    }
    if include_meta:
        answer_properties["meta"] = {
            "type": "object",
            "properties": {"disable_link_preview": {"type": "boolean"}},
            "required": [],
            "additionalProperties": False,
        }
    properties: dict[str, Any] = {
        "answer": {
            "type": "object",
            "properties": answer_properties,
            "required": ["kind", "content"],
            "additionalProperties": False,
        },
        "should_answer_to_user": {"type": "boolean"},
    }
    if include_attachments:
        properties["attachments"] = attachment_array_schema()
    return {
        "type": "object",
        "properties": properties,
        "required": ["answer", "should_answer_to_user"],
        "additionalProperties": False,
    }


def payload_to_object(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        parsed = parse_json_maybe_python_object(payload)
        if isinstance(parsed, dict):
            return parsed
    return None


def coerce_should_answer(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return None


def validate_attachments(raw_attachments: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_attachments, list):
        return []

    validated: list[dict[str, Any]] = []
    for item in raw_attachments:
        if not isinstance(item, dict):
            continue

        path = item.get("path")
        file_type = item.get("type")
        if not isinstance(path, str) or not path.strip():
            continue
        if not isinstance(file_type, str) or not file_type.strip():
            continue

        attachment = {
            "path": path.strip(),
            "type": file_type.strip(),
        }
        caption = item.get("caption")
        if isinstance(caption, str) and caption.strip():
            attachment["caption"] = caption.strip()
        validated.append(attachment)

    return validated
