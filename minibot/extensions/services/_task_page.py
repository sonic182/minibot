from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from minibot.adapters.tasks.sqlite_store import SQLiteTaskStore
from minibot.app.tasks.manager import TaskManager
from minibot.core.tasks import AmbiguousTaskIdError, TaskRecord, TaskStatus

_ACTIVE_STATUSES = [TaskStatus.PENDING, TaskStatus.LEASED, TaskStatus.RUNNING, TaskStatus.RETRYING]
_LIST_LIMIT = 100
_PREVIEW_CHARS = 160


def build_task_page(store: SQLiteTaskStore, manager: TaskManager, owner_id: str) -> Any:
    from starlette.responses import PlainTextResponse, RedirectResponse

    from minibot.adapters.http import page_url, render

    async def _page(request: Any) -> Any:
        if request.method == "POST":
            form = await request.form()
            task_id = str(form.get("id") or "")
            if form.get("action") == "cancel" and task_id:
                try:
                    await _cancel(store, manager, task_id, owner_id)
                except AmbiguousTaskIdError:
                    return PlainTextResponse("ambiguous task id", status_code=400)
            return RedirectResponse(page_url(request), status_code=303)

        task_id = request.query_params.get("id", "").strip()
        if task_id:
            try:
                record = await store.get(task_id, owner_id)
            except AmbiguousTaskIdError:
                return PlainTextResponse("ambiguous task id", status_code=400)
            if record is None:
                return PlainTextResponse("task not found", status_code=404)
            events = await store.events(record.request.task_id)
            return render(request, "task_detail.html", {"task": _row(record), "events": events})

        show_all = request.query_params.get("status") == "all"
        active = await store.list(owner_id=owner_id, statuses=_ACTIVE_STATUSES, limit=_LIST_LIMIT)
        records = await store.list(owner_id=owner_id, statuses=None, limit=_LIST_LIMIT) if show_all else active
        context = {
            "owner_id": owner_id,
            "tasks": [_row(record) for record in records],
            "active_count": len(active),
            "show_all": show_all,
        }
        return render(request, "tasks.html", context)

    return _page


async def _cancel(store: SQLiteTaskStore, manager: TaskManager, task_id: str, owner_id: str) -> None:
    record = await store.get(task_id, owner_id)
    if record is None or record.status not in _ACTIVE_STATUSES:
        return
    if not await manager.cancel(record.request.task_id):
        await store.mark_cancelled(record.request.task_id)


def _row(record: TaskRecord) -> dict[str, Any]:
    request = record.request
    prompt = request.prompt.strip()
    return {
        "id": request.task_id,
        "short_id": request.task_id[:8],
        "agent": request.agent_name or "general",
        "status": record.status.value,
        "active": record.status in _ACTIVE_STATUSES,
        "channel": request.channel,
        "prompt": prompt,
        "prompt_preview": prompt if len(prompt) <= _PREVIEW_CHARS else f"{prompt[:_PREVIEW_CHARS]}…",
        "created_at": record.created_at,
        "duration": _duration(record),
        "attempts": f"{record.retry_count}/{record.max_attempts}" if record.retry_count else None,
        "stop_reason": record.stop_reason.value if record.stop_reason else None,
        "last_error": record.last_error,
        "result": record.result.text if record.result else None,
        "timeout_seconds": request.limits.timeout_seconds,
    }


def _duration(record: TaskRecord) -> str | None:
    if record.started_at is None:
        return None
    end = record.completed_at or datetime.now(UTC)
    seconds = max(int((end - record.started_at).total_seconds()), 0)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"
