from __future__ import annotations

import re
from typing import Any, Sequence

from llm_async.models import Tool

from minibot.llm.tools.base import ToolBinding
from minibot.shared.json_schema import to_openai_strict_schema


_OPENAI_STRICT_MODEL_PATTERNS = (
    re.compile(r"^openai(?:/.*)?"),
    re.compile(r"^gpt-.*"),
)


def should_apply_openai_strict_schema(model_name: str | None) -> bool:
    if not isinstance(model_name, str) or not model_name:
        return False
    return any(pattern.match(model_name) for pattern in _OPENAI_STRICT_MODEL_PATTERNS)


def normalize_response_schema(
    response_schema: dict[str, Any] | None,
    model_name: str | None,
) -> dict[str, Any] | None:
    if isinstance(response_schema, dict) and should_apply_openai_strict_schema(model_name):
        return to_openai_strict_schema(response_schema)
    return response_schema


def prepare_tool_specs(tool_bindings: Sequence[ToolBinding], model_name: str | None) -> list[Tool] | None:
    if not tool_bindings:
        return None
    if not should_apply_openai_strict_schema(model_name):
        return [binding.tool for binding in tool_bindings]

    strict_tools: list[Tool] = []
    for binding in tool_bindings:
        parameters = binding.tool.parameters
        if isinstance(parameters, dict):
            parameters = to_openai_strict_schema(parameters)
        strict_tools.append(
            Tool(
                name=binding.tool.name,
                description=binding.tool.description,
                parameters=parameters,
            )
        )
    return strict_tools
