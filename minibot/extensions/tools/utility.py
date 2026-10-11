from __future__ import annotations

from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.calculator import CalculatorTool
from minibot.llm.tools.docs_reader import DocsReaderTool
from minibot.llm.tools.time import CurrentTimeTool
from minibot.llm.tools.wait import WaitTool


def register(mb: ExtensionContext) -> None:
    tools = mb.settings.tools
    if tools.time.enabled:
        mb.add_tool(CurrentTimeTool(tools.time.default_format).bindings())
    if tools.wait.enabled:
        mb.add_tool(WaitTool(max_milliseconds=tools.wait.max_milliseconds).bindings())
    if tools.calculator.enabled:
        mb.add_tool(
            CalculatorTool(
                default_scale=tools.calculator.default_scale,
                max_expression_length=tools.calculator.max_expression_length,
                max_exponent_abs=tools.calculator.max_exponent_abs,
            ).bindings()
        )
    skills = tools.skills
    if (
        mb.entrypoint != "worker"
        and skills.enabled
        and skills.native
        and "minibot-docs" not in skills.disabled_native_skills
    ):
        mb.add_tool(DocsReaderTool().bindings())
