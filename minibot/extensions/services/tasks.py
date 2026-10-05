from __future__ import annotations

from minibot.adapters.agents.definition_reader import LocalAgentDefinitionReader
from minibot.adapters.memory.sqlalchemy import SQLAlchemyMemoryBackend
from minibot.adapters.messaging.telegram.capabilities import TELEGRAM_CHANNEL_CAPABILITIES
from minibot.adapters.tasks.retention import TaskRetentionService
from minibot.adapters.tasks.sqlite_store import SQLiteTaskProducer, SQLiteTaskStore
from minibot.app.agent_definitions_loader import load_active_agent_specs
from minibot.app.agent_registry import AgentRegistry
from minibot.app.extensions import ExtensionContext
from minibot.app.llm_client_factory import available_providers
from minibot.app.task_consumer_service import SQLiteTaskConsumerService
from minibot.app.tasks.manager import TaskManager, resolve_delegation_budget
from minibot.config.schema import Settings
from minibot.llm.tools.tasks import TaskTools


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
    store = SQLiteTaskStore(settings.tasks.sqlite)
    agent_registry = mb.agent_registry or AgentRegistry(
        load_active_agent_specs(settings, reader=LocalAgentDefinitionReader())
    )
    manager = TaskManager(
        mb.event_bus,
        settings.tasks.worker_timeout_seconds,
        store,
        settings.tasks.sqlite.lease_timeout_seconds,
        secrets=mb.vault.as_mapping() if mb.vault else None,
        budget_for=lambda name, ov: resolve_delegation_budget(agent_registry, settings, name, ov),
        approval_timeout_seconds=settings.tools.approval.timeout_seconds,
        channel_capabilities={"telegram": TELEGRAM_CHANNEL_CAPABILITIES},
        history_store=build_task_history_store(settings),
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
            agent_registry=agent_registry,
            specialists_enabled=settings.orchestration.specialists.enabled,
            providers=available_providers(settings),
        ).bindings()
    )
    mb.add_service(_SQLiteTasksService(store, consumer))
