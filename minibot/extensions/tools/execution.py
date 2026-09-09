from __future__ import annotations

from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.apply_patch import ApplyPatchTool
from minibot.llm.tools.bash import BashTool
from minibot.llm.tools.python_exec import HostPythonExecTool

from ._storage import managed_storage


def register(mb: ExtensionContext) -> None:
    storage = managed_storage(mb.settings)
    if mb.settings.tools.python_exec.enabled:
        mb.add_tool(HostPythonExecTool(mb.settings.tools.python_exec, storage=storage).bindings())
    if mb.settings.tools.bash.enabled:
        mb.add_tool(BashTool(mb.settings.tools.bash, storage=storage).bindings())
    if mb.settings.tools.apply_patch.enabled:
        mb.add_tool(ApplyPatchTool(mb.settings.tools.apply_patch).bindings())
