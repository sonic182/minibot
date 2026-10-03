from __future__ import annotations

from minibot.adapters.tasks.retention import TaskRetentionService
from minibot.adapters.tasks.sqlite_store import SQLiteTaskStore
from minibot.app.agent_definitions_loader import load_active_agent_specs
from minibot.app.agent_registry import AgentRegistry
from minibot.app.extensions import ExtensionContext
from minibot.app.llm_client_factory import available_providers


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tasks.enabled or mb.settings.tasks.backend != "rabbitmq":
        return
    from minibot.adapters.messaging.rabbitmq.producer import RabbitMQTaskProducer
    from minibot.adapters.messaging.rabbitmq.service import RabbitMQConsumerService
    from minibot.adapters.messaging.telegram.capabilities import TELEGRAM_CHANNEL_CAPABILITIES
    from minibot.app.tasks.manager import TaskManager, resolve_delegation_budget
    from minibot.llm.tools.tasks import TaskTools

    settings = mb.settings
    store = SQLiteTaskStore(settings.tasks.sqlite)
    agent_registry = mb.agent_registry or AgentRegistry(load_active_agent_specs(settings))
    manager = TaskManager(
        mb.event_bus,
        settings.tasks.worker_timeout_seconds,
        store,
        settings.tasks.sqlite.lease_timeout_seconds,
        secrets=mb.vault.as_mapping() if mb.vault else None,
        budget_for=lambda name, ov: resolve_delegation_budget(agent_registry, settings, name, ov),
        channel_capabilities={"telegram": TELEGRAM_CHANNEL_CAPABILITIES},
        approval_timeout_seconds=settings.tools.approval.timeout_seconds,
    )
    producer = RabbitMQTaskProducer(settings.rabbitmq, store)
    consumer = RabbitMQConsumerService(
        settings.rabbitmq,
        mb.event_bus,
        store,
        manager,
        settings.tasks.max_concurrent_workers,
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
    mb.add_service(consumer)
