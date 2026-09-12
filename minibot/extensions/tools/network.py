from __future__ import annotations

from minibot.app.extensions import ExtensionContext

from ._storage import managed_storage


def register(mb: ExtensionContext) -> None:
    if not mb.settings.tools.http_client.enabled:
        return
    # Imported only once the tool is enabled so a disabled HTTP tool contributes no import cost.
    # selectolax (a core dependency) is itself imported lazily on first HTML compaction.
    from minibot.llm.tools.http_client import HTTPClientTool

    mb.add_tool(HTTPClientTool(mb.settings.tools.http_client, storage=managed_storage(mb.settings)).bindings())
