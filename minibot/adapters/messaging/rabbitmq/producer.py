from __future__ import annotations

import json
from dataclasses import asdict

import aio_pika

from minibot.adapters.config.schema import RabbitMQConsumerConfig
from minibot.core.tasks import TaskRepository, TaskRequest, TaskStopReason


class RabbitMQTaskProducer:
    """Publishes task requests to the fanout exchange consumed by ``RabbitMQConsumerService``."""

    def __init__(self, config: RabbitMQConsumerConfig, task_repository: TaskRepository | None = None) -> None:
        self._config = config
        self._task_repository = task_repository

    async def enqueue(self, task: TaskRequest) -> None:
        if self._task_repository is not None:
            await self._task_repository.create(task)
            body = asdict(task)
        else:
            body = {
                "task_id": task.task_id,
                "channel": task.channel,
                "prompt": task.prompt,
                "agent_name": task.agent_name,
                "context": task.context,
                "chat_id": task.chat_id,
                "user_id": task.user_id,
            }
        try:
            connection = await aio_pika.connect_robust(self._config.broker_url)
            async with connection:
                channel = await connection.channel()
                exchange = await channel.declare_exchange(
                    self._config.exchange_name,
                    aio_pika.ExchangeType.FANOUT,
                    durable=True,
                )
                await exchange.publish(
                    aio_pika.Message(
                        body=json.dumps(body, ensure_ascii=True, sort_keys=True).encode(),
                        content_type="application/json",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key="",
                )
        except Exception as exc:
            if self._task_repository is not None:
                await self._task_repository.mark_failed(task.task_id, str(exc), TaskStopReason.WORKER_ERROR)
            raise
