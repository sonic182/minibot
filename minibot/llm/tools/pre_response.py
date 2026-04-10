from __future__ import annotations

from typing import Any

from llm_async.models import Tool

from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.schema_utils import attachment_array_schema, strict_object


def pre_response_binding() -> ToolBinding:
    return ToolBinding(tool=_schema(), handler=_handler)


def _schema() -> Tool:
    return Tool(
        name="pre_response",
        description=(
            "Call this tool immediately before writing your final answer to declare response format metadata. "
            "Use kind='markdown' for markdown-formatted responses, 'html' for HTML, or 'text' for plain text. "
            "Set meta.disable_link_preview=true to suppress link previews. "
            "Include attachments to send files alongside the response."
        ),
        parameters=strict_object(
            properties={
                "kind": {
                    "type": "string",
                    "enum": ["text", "html", "markdown"],
                    "description": "Output format for the final answer.",
                },
                "meta": {
                    "type": "object",
                    "properties": {
                        "disable_link_preview": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                "attachments": attachment_array_schema(),
            },
            required=["kind"],
        ),
    )


async def _handler(payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    kind = payload.get("kind") or "markdown"
    meta = payload.get("meta")
    attachments = payload.get("attachments")
    return {
        "kind": kind,
        "meta": meta if isinstance(meta, dict) else {},
        "attachments": attachments if isinstance(attachments, list) else [],
    }
