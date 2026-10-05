from __future__ import annotations

from minibot.adapters.memory.sqlalchemy import SQLAlchemyMemoryBackend
from minibot.adapters.tasks.sqlite_store import SQLiteTaskProducer, SQLiteTaskStore
from minibot.app.extensions import ExtensionContext
from minibot.app.task_consumer_service import SQLiteTaskConsumerService
from minibot.config.schema import Settings
from minibot.extensions.services._task_wiring import wire_task_runtime


def build_task_history_store(settings: Settings) -> SQLAlchemyMemoryBackend | None:
    return SQLAlchemyMemoryBackend(settings.memory) if settings.tasks.history else None


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
    store, manager = wire_task_runtime(mb, SQLiteTaskProducer, history_store=build_task_history_store(settings))
    consumer = SQLiteTaskConsumerService(
        store=store,
        task_manager=manager,
        config=settings.tasks.sqlite,
        max_concurrent_workers=settings.tasks.max_concurrent_workers,
    )
    mb.add_service(_SQLiteTasksService(store, consumer))
