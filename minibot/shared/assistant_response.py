from __future__ import annotations

from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from minibot.llm.tools.schema_utils import attachment_array_schema


class AssistantAnswerMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disable_link_preview: bool | None = None


class AssistantAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["text", "html", "markdown"]
    content: str | None = None
    meta: AssistantAnswerMeta = Field(default_factory=AssistantAnswerMeta)

    @field_validator("meta", mode="before")
    @classmethod
    def _normalize_meta(cls, value: Any) -> Any:
        if value is None:
            return {}
        return value


class AssistantRuntimePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: AssistantAnswer | None = None
    should_continue: bool
    attachments: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("attachments", mode="before")
    @classmethod
    def _normalize_attachments(cls, value: Any) -> Any:
        if value is None:
            return []
        return value

    @model_validator(mode="after")
    def _validate_terminal_visibility(self) -> "AssistantRuntimePayload":
        if self.should_continue:
            return self
        content = self.answer.content if self.answer is not None else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("final responses with should_continue=false must include a non-empty answer.content")
        return self


def assistant_response_schema(
    *,
    kinds: Sequence[str],
    include_meta: bool = False,
    include_attachments: bool = False,
) -> dict[str, Any]:
    answer_properties: dict[str, Any] = {
        "kind": {"type": "string", "enum": list(kinds)},
        "content": {"type": ["string", "null"]},
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
            "anyOf": [
                {
                    "type": "object",
                    "properties": answer_properties,
                    "required": ["kind"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ],
        },
        "should_continue": {"type": "boolean"},
    }
    if include_attachments:
        properties["attachments"] = attachment_array_schema()
    return {
        "type": "object",
        "properties": properties,
        "required": ["should_continue"],
        "additionalProperties": False,
    }


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
