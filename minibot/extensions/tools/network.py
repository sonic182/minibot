from __future__ import annotations

from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.http_client import HTTPClientTool

from ._storage import managed_storage


def register(mb: ExtensionContext) -> None:
    if not mb.settings.tools.http_client.enabled:
        return
    mb.add_tool(HTTPClientTool(mb.settings.tools.http_client, storage=managed_storage(mb.settings)).bindings())
