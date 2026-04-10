from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from multiprocessing import Process
from typing import Optional

import aio_pika
import aio_pika.abc
from aiopipe import aioduplex

from minibot.adapters.config.schema import RabbitMQConsumerConfig
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent


def _worker(pipe, body: bytes) -> None:
    asyncio.run(_worker_task(pipe, body))


async def _worker_task(pipe, body: bytes) -> None:
    async with pipe.open() as (rx, tx):
        raw = await rx.readline()
        payload = json.loads(raw)
        result = {"text": payload.get("text", "")}
        tx.write(json.dumps(result).encode() + b"\n")


class RabbitMQConsumerService:
    def __init__(self, config: RabbitMQConsumerConfig, event_bus: EventBus) -> None:
        self._config = config
        self._event_bus = event_bus
        self._logger = logging.getLogger("minibot.rabbitmq")
        self._consume_task: Optional[asyncio.Task[None]] = None

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
            queue: aio_pika.abc.AbstractQueue = await channel.declare_queue(
                self._config.queue_name, durable=True
            )
            self._logger.info("rabbitmq consumer started", extra={"queue": self._config.queue_name})
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    async with message.process():
                        await self._dispatch(message)

    async def _dispatch(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        mainpipe, chpipe = aioduplex()

        with chpipe.detach() as chpipe:
            proc = Process(target=_worker, args=(chpipe, message.body), daemon=True)
            proc.start()

        try:
            async with mainpipe.open() as (rx, tx):
                tx.write(message.body + b"\n")
                raw = await asyncio.wait_for(rx.readline(), timeout=self._config.worker_timeout_seconds)
        except asyncio.TimeoutError:
            self._logger.warning(
                "worker timed out",
                extra={"queue": self._config.queue_name, "delivery_tag": message.delivery_tag},
            )
            proc.terminate()
            proc.join()
            return

        proc.join()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            self._logger.warning("worker returned invalid JSON", extra={"raw": raw[:200]})
            return

        try:
            body = json.loads(message.body)
        except (json.JSONDecodeError, ValueError):
            body = {}

        channel_message = ChannelMessage(
            channel="rabbitmq",
            user_id=body.get("user_id"),
            chat_id=body.get("chat_id"),
            message_id=message.delivery_tag,
            text=result.get("text", ""),
            metadata={
                "routing_key": message.routing_key,
                "correlation_id": message.correlation_id,
                "reply_to": message.reply_to,
            },
        )
        await self._event_bus.publish(MessageEvent(message=channel_message))
