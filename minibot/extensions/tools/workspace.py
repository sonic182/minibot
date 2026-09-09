from __future__ import annotations

from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.code_read import CodeReadTool
from minibot.llm.tools.file_storage import FileStorageTool
from minibot.llm.tools.grep import GrepTool

from ._storage import managed_storage

_WORKER_TOOL_NAMES = {"filesystem", "glob_files", "read_file", "code_read", "grep"}


def register(mb: ExtensionContext) -> None:
    settings = mb.settings
    storage = managed_storage(settings)
    bindings = []
    if storage is not None:
        bindings.extend(FileStorageTool(storage=storage, event_bus=mb.event_bus).bindings())
        bindings.extend(CodeReadTool(storage=storage).bindings())
    if settings.tools.grep.enabled:
        storage = managed_storage(settings, error_message="tools.grep.enabled requires tools.file_storage.enabled")
        bindings.extend(GrepTool(storage=storage, config=settings.tools.grep).bindings())
    if mb.entrypoint == "worker":
        bindings = [binding for binding in bindings if binding.tool.name in _WORKER_TOOL_NAMES]
    mb.add_tool(bindings)
