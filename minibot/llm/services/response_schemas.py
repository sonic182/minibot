from __future__ import annotations

from typing import Any
from pydantic import BaseModel

from minibot.shared.assistant_response import AssistantRuntimePayload, assistant_response_schema


def main_assistant_response_schema() -> dict[str, Any]:
    return assistant_response_schema(kinds=["text", "html", "markdown"], include_meta=True)


def main_assistant_response_model() -> type[BaseModel]:
    return AssistantRuntimePayload


def delegated_agent_response_schema() -> dict[str, Any]:
    return assistant_response_schema(kinds=["text", "html", "markdown", "json"], include_attachments=True)
