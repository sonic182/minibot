from __future__ import annotations

import hmac
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
    from secrets import token_urlsafe

    from starlette.responses import PlainTextResponse, RedirectResponse

    from minibot.adapters.http import page_url, render

    csrf_token = token_urlsafe()

    async def _page(request: Any) -> Any:
        try:
            offset = max(0, int(request.query_params.get("offset", 0) or 0))
        except ValueError:
            return PlainTextResponse("offset must be an integer", status_code=400)
        query = request.query_params.get("q", "").strip()
        # Offset rather than a cursor: search results are ranked by relevance, not by a stable key.
        current_url = page_url(request, offset=offset)
        if request.method == "POST":
            form = await request.form()
            submitted_token = str(form.get("csrf_token") or "")
            if not hmac.compare_digest(submitted_token, csrf_token):
                return PlainTextResponse("invalid csrf token", status_code=403)
            entry_id = str(form.get("id") or "")
            data = str(form.get("data") or "")
            action = form.get("action")
            if action == "delete" and entry_id:
                await memory.delete_entry(owner_id, entry_id)
            elif action == "update" and entry_id and data:
                await memory.update_entry(owner_id, entry_id, data=data)
            else:
                return PlainTextResponse("invalid memory action", status_code=400)
            # Redirect so a reload does not resubmit the edit or the delete.
            return RedirectResponse(current_url, status_code=303)
        if query:
            result = await memory.search_entries(owner_id, query=query, offset=offset)
        else:
            result = await memory.list_entries(owner_id, offset=offset)
        shown = offset + len(result.entries)
        context = {
            "owner_id": owner_id,
            "entries": [_row(entry) for entry in result.entries],
            "total": result.total,
            "offset": offset,
            "q": query,
            "current_url": current_url,
            "next_url": page_url(request, offset=shown) if shown < result.total else None,
            "csrf_token": csrf_token,
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
