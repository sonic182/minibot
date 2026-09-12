from __future__ import annotations

from minibot.adapters.tasks.manager import TaskManager
from minibot.adapters.tasks.retention import TaskRetentionService
from minibot.adapters.tasks.sqlite_store import SQLiteTaskProducer, SQLiteTaskStore
from minibot.app.agent_definitions_loader import load_agent_specs
from minibot.app.agent_registry import AgentRegistry
from minibot.app.extensions import ExtensionContext
from minibot.app.task_consumer_service import SQLiteTaskConsumerService
from minibot.llm.tools.tasks import TaskTools


class _SQLiteTasksService:
    def __init__(self, store: SQLiteTaskStore, consumer: SQLiteTaskConsumerService) -> None:
        self._store = store
        self._consumer = consumer

    async def start(self) -> None:
        await self._store.initialize()
        await self._consumer.start()

    async def stop(self) -> None:
        await self._consumer.stop()


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tasks.enabled or mb.settings.tasks.backend != "sqlite":
        return
    settings = mb.settings
    store = SQLiteTaskStore(settings.tasks.sqlite)
    manager = TaskManager(
        mb.event_bus,
        settings.tasks.worker_timeout_seconds,
        store,
        settings.tasks.sqlite.lease_timeout_seconds,
    )
    producer = SQLiteTaskProducer(store)
    consumer = SQLiteTaskConsumerService(
        store=store,
        task_manager=manager,
        config=settings.tasks.sqlite,
        max_concurrent_workers=settings.tasks.max_concurrent_workers,
    )
    mb.add_service(TaskRetentionService(store, settings.tasks.sqlite.done_retention_seconds))
    mb.add_tool(
        TaskTools(
            producer=producer,
            task_manager=manager,
            task_repository=store,
            config=settings.tasks,
            agent_registry=AgentRegistry(load_agent_specs(settings.orchestration.directory)),
        ).bindings()
    )
    mb.add_service(_SQLiteTasksService(store, consumer))
