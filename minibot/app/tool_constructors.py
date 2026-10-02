from __future__ import annotations

from minibot.app.skill_registry import SkillRegistry
from minibot.core.files import FileStorage
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.calculator import CalculatorTool
from minibot.llm.tools.skill_loader import SkillLoaderTool


def build_calculator_tool(default_scale: int, max_expression_length: int, max_exponent_abs: int) -> CalculatorTool:
    return CalculatorTool(
        default_scale=default_scale,
        max_expression_length=max_expression_length,
        max_exponent_abs=max_exponent_abs,
    )


def build_skill_loader_bindings(
    registry: SkillRegistry,
    storage: FileStorage | None,
    bash_enabled: bool,
) -> list[ToolBinding]:
    return SkillLoaderTool(registry, storage, bash_enabled).bindings()
