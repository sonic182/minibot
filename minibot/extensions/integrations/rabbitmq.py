from __future__ import annotations

from minibot.app.extensions import ExtensionContext
from minibot.extensions.services._task_wiring import wire_task_runtime


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tasks.enabled or mb.settings.tasks.backend != "rabbitmq":
        return
    from minibot.adapters.messaging.rabbitmq.producer import RabbitMQTaskProducer
    from minibot.adapters.messaging.rabbitmq.service import RabbitMQConsumerService

    settings = mb.settings
    store, manager = wire_task_runtime(mb, lambda store: RabbitMQTaskProducer(settings.rabbitmq, store))
    mb.add_service(
        RabbitMQConsumerService(
            settings.rabbitmq,
            mb.event_bus,
            store,
            manager,
            settings.tasks.max_concurrent_workers,
        )
    )
