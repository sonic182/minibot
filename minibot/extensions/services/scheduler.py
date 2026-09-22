from __future__ import annotations

from typing import Any

from minibot.adapters.scheduler.sqlalchemy_prompt_store import SQLAlchemyScheduledPromptStore
from minibot.app.extensions import ExtensionContext
from minibot.app.scheduler_service import ScheduledPromptService
from minibot.core.jobs import PromptRecurrence, ScheduledPrompt, ScheduledPromptStatus
from minibot.llm.tools.scheduler import SchedulePromptTool


class _SchedulerService:
    def __init__(
        self,
        store: SQLAlchemyScheduledPromptStore,
        scheduler: ScheduledPromptService,
        entrypoint: str,
    ) -> None:
        self._store = store
        self._scheduler = scheduler
        self._entrypoint = entrypoint

    async def start(self) -> None:
        await self._store.initialize()
        if self._entrypoint == "daemon":
            await self._scheduler.start()

    async def stop(self) -> None:
        if self._entrypoint == "daemon":
            await self._scheduler.stop()


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.scheduler.prompts.enabled:
        return
    config = mb.settings.scheduler.prompts
    store = SQLAlchemyScheduledPromptStore(config)
    scheduler = ScheduledPromptService(store, mb.event_bus, config)
    mb.add_tool(
        SchedulePromptTool(
            scheduler,
            min_recurrence_interval_seconds=config.min_recurrence_interval_seconds,
        ).bindings()
    )
    mb.add_service(_SchedulerService(store, scheduler, mb.entrypoint))
    if mb.settings.http.enabled:
        page = _build_page(scheduler, mb.settings.runtime.owner_id)
        mb.add_page("/scheduled", "Scheduled", page, ("GET", "POST"))


_PAGE_SIZE = 50


def _build_page(service: ScheduledPromptService, owner_id: str) -> Any:
    # Imported here so the starlette/jinja extra is only required when the server is switched on.
    from starlette.responses import PlainTextResponse, RedirectResponse

    from minibot.adapters.http import page_url, render

    async def _page(request: Any) -> Any:
        show_all = request.query_params.get("status") == "all"
        query = request.query_params.get("q", "").strip()
        try:
            offset = max(0, int(request.query_params.get("offset", 0) or 0))
        except ValueError:
            return PlainTextResponse("offset must be an integer", status_code=400)
        # Offset rather than a cursor: a recurring job's run_at, the sort key, moves on every fire.
        current_url = page_url(request, offset=offset)
        if request.method == "POST":
            form = await request.form()
            job_id = str(form.get("id") or "")
            if form.get("action") == "cancel" and job_id:
                # The service checks the job belongs to this owner; the raw store method does not.
                await service.cancel_prompt(job_id=job_id, owner_id=owner_id)
            # Redirect so a reload does not resubmit the cancel.
            return RedirectResponse(current_url, status_code=303)

        jobs = await service.list_prompts(
            owner_id=owner_id, active_only=not show_all, limit=_PAGE_SIZE + 1, offset=offset, query=query or None
        )
        context = {
            "owner_id": owner_id,
            "jobs": [_row(job) for job in jobs[:_PAGE_SIZE]],
            "show_all": show_all,
            "q": query,
            "current_url": current_url,
            "next_url": page_url(request, offset=offset + _PAGE_SIZE) if len(jobs) > _PAGE_SIZE else None,
        }
        return render(request, "scheduled.html", context)

    return _page


def _row(job: ScheduledPrompt) -> dict[str, Any]:
    # `run_at` is already the next fire time: the service overwrites it after every dispatch, so
    # nothing here has to re-derive an interval or parse a cron expression.
    return {
        "id": job.id,
        "status": job.status.value,
        "active": job.status in {ScheduledPromptStatus.PENDING, ScheduledPromptStatus.LEASED},
        "run_at": job.run_at,
        "text": job.text,
        "channel": job.channel,
        "chat_id": job.chat_id,
        "recurrence": _describe_recurrence(job),
        "recurrence_end_at": job.recurrence_end_at,
        "attempts": f"{job.retry_count}/{job.max_attempts}" if job.retry_count else None,
        "last_error": job.last_error,
    }


def _describe_recurrence(job: ScheduledPrompt) -> str | None:
    if job.recurrence is PromptRecurrence.INTERVAL and job.recurrence_interval_seconds:
        return f"every {job.recurrence_interval_seconds}s"
    if job.recurrence is PromptRecurrence.CRON and job.recurrence_cron_expression:
        return f"cron {job.recurrence_cron_expression}"
    return None
