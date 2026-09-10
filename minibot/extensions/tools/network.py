from __future__ import annotations

from minibot.app.extensions import ExtensionContext

from ._storage import managed_storage


def register(mb: ExtensionContext) -> None:
    if not mb.settings.tools.http_client.enabled:
        return
    # Imported only once the tool is enabled: the HTTP tool's HTML compaction pulls in
    # selectolax, which lives in the `http` extra. Without it the tool still works and
    # falls back to plain text extraction.
    from minibot.llm.tools.http_client import HTTPClientTool

    mb.add_tool(HTTPClientTool(mb.settings.tools.http_client, storage=managed_storage(mb.settings)).bindings())
