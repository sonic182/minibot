from __future__ import annotations

from minibot.adapters.scheduler.sqlalchemy_prompt_store import SQLAlchemyScheduledPromptStore
from minibot.app.extensions import ExtensionContext
from minibot.app.scheduler_service import ScheduledPromptService
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
