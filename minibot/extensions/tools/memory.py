from __future__ import annotations

from typing import Any

from minibot.adapters.memory.kv_sqlalchemy import SQLAlchemyKeyValueMemory
from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.user_memory import build_kv_tools


class _MemoryService:
    def __init__(self, memory: SQLAlchemyKeyValueMemory) -> None:
        self._memory = memory

    async def start(self) -> None:
        await self._memory.initialize()

    async def stop(self) -> None:
        return None


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tools.kv_memory.enabled:
        return
    memory = SQLAlchemyKeyValueMemory(mb.settings.tools.kv_memory)
    mb.add_tool(build_kv_tools(memory))
    mb.add_service(_MemoryService(memory))
    if mb.settings.http.enabled:
        mb.add_page("/memory", "Memory", _build_page(memory, mb.settings.runtime.owner_id), ("GET", "POST"))


def _build_page(memory: SQLAlchemyKeyValueMemory, owner_id: str) -> Any:
    # Imported here so the starlette/jinja extra is only required when the server is switched on.
    from starlette.responses import RedirectResponse

    from minibot.adapters.http import render

    async def _page(request: Any) -> Any:
        offset = max(0, int(request.query_params.get("offset", 0) or 0))
        if request.method == "POST":
            form = await request.form()
            entry_id = str(form.get("id") or "")
            data = str(form.get("data") or "")
            if form.get("action") == "delete" and entry_id:
                await memory.delete_entry(owner_id, entry_id)
            elif entry_id and data:
                await memory.update_entry(owner_id, entry_id, data=data)
            # Redirect so a reload does not resubmit the edit or the delete.
            return RedirectResponse(f"/memory?offset={offset}", status_code=303)
        result = await memory.list_entries(owner_id, offset=offset)
        shown = offset + len(result.entries)
        context = {
            "owner_id": owner_id,
            "entries": [_row(entry) for entry in result.entries],
            "total": result.total,
            "offset": offset,
            "previous_offset": max(0, offset - result.limit),
            "next_offset": shown if shown < result.total else None,
        }
        return render(request, "memory.html", context)

    return _page


def _row(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.id,
        "title": entry.title,
        "data": entry.data,
        "category": entry.metadata.get("category") if entry.metadata else None,
        "source": entry.source,
        "updated_at": entry.updated_at,
        "expires_at": entry.expires_at,
    }
