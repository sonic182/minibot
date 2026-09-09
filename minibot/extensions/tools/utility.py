from __future__ import annotations

from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.time import CurrentTimeTool
from minibot.llm.tools.wait import WaitTool


def register(mb: ExtensionContext) -> None:
    if mb.settings.tools.time.enabled:
        mb.add_tool(CurrentTimeTool(mb.settings.tools.time.default_format).bindings())
    if mb.entrypoint != "worker" and mb.settings.tools.wait.enabled:
        mb.add_tool(WaitTool(max_milliseconds=mb.settings.tools.wait.max_milliseconds).bindings())
