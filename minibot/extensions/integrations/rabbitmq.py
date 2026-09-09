from __future__ import annotations

from minibot.app.agent_definitions_loader import load_agent_specs
from minibot.app.agent_registry import AgentRegistry
from minibot.app.extensions import ExtensionContext


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tasks.enabled or mb.settings.tasks.backend != "rabbitmq":
        return
    from minibot.adapters.messaging.rabbitmq.producer import RabbitMQTaskProducer
    from minibot.adapters.messaging.rabbitmq.service import RabbitMQConsumerService
    from minibot.adapters.tasks.manager import TaskManager
    from minibot.llm.tools.tasks import TaskTools

    settings = mb.settings
    manager = TaskManager(mb.event_bus, settings.tasks.worker_timeout_seconds)
    producer = RabbitMQTaskProducer(settings.rabbitmq)
    consumer = RabbitMQConsumerService(
        settings.rabbitmq,
        mb.event_bus,
        manager,
        settings.tasks.max_concurrent_workers,
    )
    mb.add_tool(
        TaskTools(
            producer=producer,
            task_manager=manager,
            agent_registry=AgentRegistry(load_agent_specs(settings.orchestration.directory)),
        ).bindings()
    )
    mb.add_service(consumer)
