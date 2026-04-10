from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import aio_pika
import aio_pika.abc

from minibot.adapters.config.schema import RabbitMQConsumerConfig
from minibot.adapters.tasks.manager import TaskManager
from minibot.app.event_bus import EventBus


class RabbitMQConsumerService:
    def __init__(
        self,
        config: RabbitMQConsumerConfig,
        event_bus: EventBus,
        task_manager: TaskManager | None = None,
    ) -> None:
        self._config = config
        self._event_bus = event_bus
        self._task_manager = task_manager
        self._logger = logging.getLogger("minibot.rabbitmq")
        self._consume_task: asyncio.Task[None] | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None
        self._semaphore: asyncio.Semaphore = asyncio.Semaphore(config.max_concurrent_workers)

    async def start(self) -> None:
        self._consume_task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._consume_task is not None:
            self._consume_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consume_task

    async def _consume(self) -> None:
        connection: aio_pika.abc.AbstractRobustConnection = await aio_pika.connect_robust(
            self._config.broker_url
        )
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=self._config.prefetch_count)
            self._exchange = await channel.declare_exchange(
                self._config.exchange_name, aio_pika.ExchangeType.FANOUT, durable=True
            )
            queue: aio_pika.abc.AbstractQueue = await channel.declare_queue(
                self._config.queue_name, durable=True
            )
            await queue.bind(self._exchange)
            self._logger.info(
                "rabbitmq consumer started",
                extra={"queue": self._config.queue_name, "exchange": self._config.exchange_name},
            )
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    await self._dispatch(message)

    async def _dispatch(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            body = json.loads(message.body)
        except (json.JSONDecodeError, ValueError):
            self._logger.warning("malformed rabbitmq message: invalid JSON, discarding")
            await message.nack(requeue=False)
            return

        task_id = body.get("task_id")
        prompt = body.get("prompt")
        if not task_id or not prompt:
            self._logger.warning(
                "malformed rabbitmq message: missing required fields",
                extra={"missing": [f for f in ("task_id", "prompt") if not body.get(f)]},
            )
            await message.nack(requeue=False)
            return

        chat_id: int | None = body.get("chat_id")
        user_id: int | None = body.get("user_id")
        context: dict[str, Any] = body.get("context", {})

        await self._semaphore.acquire()

        ack_cb = message.ack

        async def nack_cb() -> None:
            await message.nack(requeue=True)

        if self._task_manager is not None:
            await self._task_manager.spawn(
                task_id=task_id,
                prompt=prompt,
                context=context,
                chat_id=chat_id,
                user_id=user_id,
                ack_cb=ack_cb,
                nack_cb=nack_cb,
                semaphore=self._semaphore,
            )
        else:
            self._semaphore.release()
            self._logger.warning("no task manager configured, discarding message", extra={"task_id": task_id})
            await message.nack(requeue=False)
