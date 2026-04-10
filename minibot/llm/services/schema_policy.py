from __future__ import annotations

import re
from typing import Sequence

from llm_async.models import Tool

from minibot.llm.tools.base import ToolBinding
from minibot.shared.json_schema import to_openai_strict_schema, to_relaxed_schema


_OPENAI_STRICT_MODEL_PATTERNS = (
    re.compile(r"^openai(?:/.*)?"),
    re.compile(r"^gpt-.*"),
)

_DEEPSEEK_MODEL_PATTERNS = (
    re.compile(r"^deepseek(?:/.*)?"),
    re.compile(r".*/deepseek.*"),
)


def _should_apply_openai_strict_schema(model_name: str | None) -> bool:
    if not isinstance(model_name, str) or not model_name:
        return False
    return any(pattern.match(model_name) for pattern in _OPENAI_STRICT_MODEL_PATTERNS)


def _should_apply_relaxed_schema(model_name: str | None) -> bool:
    if not isinstance(model_name, str) or not model_name:
        return False
    return any(pattern.match(model_name) for pattern in _DEEPSEEK_MODEL_PATTERNS)


def prepare_tool_specs(tool_bindings: Sequence[ToolBinding], model_name: str | None) -> list[Tool] | None:
    if not tool_bindings:
        return None
    if _should_apply_openai_strict_schema(model_name):
        result: list[Tool] = []
        for binding in tool_bindings:
            parameters = binding.tool.parameters
            if isinstance(parameters, dict):
                parameters = to_openai_strict_schema(parameters)
            result.append(Tool(name=binding.tool.name, description=binding.tool.description, parameters=parameters))
        return result
    if _should_apply_relaxed_schema(model_name):
        result = []
        for binding in tool_bindings:
            parameters = binding.tool.parameters
            if isinstance(parameters, dict):
                parameters = to_relaxed_schema(parameters)
            result.append(Tool(name=binding.tool.name, description=binding.tool.description, parameters=parameters))
        return result
    return [binding.tool for binding in tool_bindings]
