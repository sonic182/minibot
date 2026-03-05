from __future__ import annotations

from typing import Any

from minibot.shared.assistant_response import assistant_response_schema


def main_assistant_response_schema() -> dict[str, Any]:
    return assistant_response_schema(kinds=["text", "html", "markdown"], include_meta=True)


def delegated_agent_response_schema() -> dict[str, Any]:
    return assistant_response_schema(kinds=["text", "html", "markdown", "json"], include_attachments=True)
