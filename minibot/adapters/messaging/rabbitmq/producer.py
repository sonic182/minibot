from __future__ import annotations

import json
from dataclasses import asdict

import aio_pika

from minibot.adapters.config.schema import RabbitMQConsumerConfig
from minibot.core.tasks import TaskRequest


class RabbitMQTaskProducer:
    """Publishes task requests to the fanout exchange consumed by ``RabbitMQConsumerService``."""

    def __init__(self, config: RabbitMQConsumerConfig) -> None:
        self._config = config

    async def enqueue(self, task: TaskRequest) -> None:
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
                    body=json.dumps(asdict(task), ensure_ascii=True, sort_keys=True).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key="",
            )
