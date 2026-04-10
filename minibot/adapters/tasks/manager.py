from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from multiprocessing import Process
from typing import Any

from aiopipe import aioduplex

from minibot.adapters.tasks.worker import worker_entry
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage, ChannelResponse
from minibot.core.events import MessageEvent, OutboundEvent

_MAX_RETRYABLE_ATTEMPTS = 2


@dataclass
class Task:
    task_id: str
    channel: str
    chat_id: int | None
    user_id: int | None
    proc: Process
    reader_task: asyncio.Task
    ack_cb: Callable[[], Any]
    nack_cb: Callable[[], Any]
    semaphore: asyncio.Semaphore
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class TaskManager:
    def __init__(self, event_bus: EventBus, worker_timeout_seconds: float) -> None:
        self._event_bus = event_bus
        self._worker_timeout_seconds = worker_timeout_seconds
        self._tasks: dict[str, Task] = {}
        self._logger = logging.getLogger("minibot.tasks")

    async def spawn(
        self,
        task_id: str,
        channel: str,
        prompt: str,
        context: dict[str, Any],
        chat_id: int | None,
        user_id: int | None,
        ack_cb: Callable[[], Any],
        nack_cb: Callable[[], Any],
        semaphore: asyncio.Semaphore,
    ) -> None:
        payload = {
            "task_id": task_id,
            "channel": channel,
            "prompt": prompt,
            "context": context,
            "chat_id": chat_id,
            "user_id": user_id,
        }
        mainpipe, proc = self._start_worker_process()

        reader = asyncio.create_task(
            self._reader(task_id, mainpipe, proc, ack_cb, nack_cb, semaphore, payload)
        )
        self._tasks[task_id] = Task(
            task_id=task_id,
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            proc=proc,
            reader_task=reader,
            ack_cb=ack_cb,
            nack_cb=nack_cb,
            semaphore=semaphore,
        )
        self._logger.info("task spawned", extra={"task_id": task_id})

    def _start_worker_process(self) -> tuple[Any, Process]:
        mainpipe, chpipe = aioduplex()
        with chpipe.detach() as detached_pipe:
            proc = Process(target=worker_entry, args=(detached_pipe,), daemon=True)
            proc.start()
        return mainpipe, proc

    async def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task.reader_task
        if self._tasks.get(task_id) is task:
            await self._cancel_task(task_id, task)
        return True

    def active(self) -> list[Task]:
        return list(self._tasks.values())

    async def _reader(
        self,
        task_id: str,
        mainpipe: Any,
        proc: Process,
        ack_cb: Callable[[], Any],
        nack_cb: Callable[[], Any],
        semaphore: asyncio.Semaphore,
        payload: dict[str, Any],
    ) -> None:
        loop = asyncio.get_running_loop()
        attempt = 1
        try:
            while True:
                async with mainpipe.open() as (rx, tx):
                    tx.write(json.dumps(payload).encode() + b"\n")
                    raw = await asyncio.wait_for(rx.readline(), timeout=self._worker_timeout_seconds)

                await loop.run_in_executor(None, proc.join)

                try:
                    result = json.loads(raw)
                except json.JSONDecodeError:
                    self._logger.warning("worker returned invalid JSON", extra={"task_id": task_id})
                    await ack_cb()
                    await self._publish_status(
                        payload=payload,
                        text="La tarea asíncrona falló por un error interno del worker.",
                        metadata={"task_id": task_id, "source": "task_worker", "status": "failed"},
                    )
                    return
                if result.get("error"):
                    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
                    retry_after_seconds = _coerce_retry_after_seconds(metadata.get("retry_after_seconds"))
                    retryable = bool(metadata.get("retryable")) and attempt < _MAX_RETRYABLE_ATTEMPTS
                    if retryable:
                        await self._publish_status(
                            payload=payload,
                            text=(
                                "La tarea asíncrona alcanzó un rate limit del proveedor. "
                                f"Reintentando en {retry_after_seconds}s."
                            ),
                            metadata={
                                "task_id": task_id,
                                "source": "task_worker",
                                "status": "retrying",
                                "attempt": attempt,
                                "retry_after_seconds": retry_after_seconds,
                            },
                        )
                        self._logger.warning(
                            "worker returned retryable error",
                            extra={
                                "task_id": task_id,
                                "attempt": attempt,
                                "retry_after_seconds": retry_after_seconds,
                                "error": str(result.get("error")),
                            },
                        )
                        attempt += 1
                        await asyncio.sleep(retry_after_seconds)
                        mainpipe, proc = self._start_worker_process()
                        continue
                    self._logger.warning(
                        "worker returned error",
                        extra={"task_id": task_id, "error": str(result.get("error")), "attempts": attempt},
                    )
                    await ack_cb()
                    await self._publish_status(
                        payload=payload,
                        text=_failure_text_from_result(result),
                        metadata={
                            "task_id": task_id,
                            "source": "task_worker",
                            "status": "failed",
                            "attempts": attempt,
                        },
                    )
                    return

                await ack_cb()
                channel_message = ChannelMessage(
                    channel=str(payload.get("channel") or "rabbitmq"),
                    user_id=payload.get("user_id"),
                    chat_id=payload.get("chat_id"),
                    message_id=None,
                    text=result.get("text", ""),
                    metadata={"task_id": task_id, "source": "task_worker", "context": payload.get("context", {})},
                )
                await self._event_bus.publish(MessageEvent(message=channel_message))
                self._logger.info("task completed", extra={"task_id": task_id, "attempts": attempt})
                return

        except TimeoutError:
            self._logger.warning("task timed out", extra={"task_id": task_id})
            proc.terminate()
            await loop.run_in_executor(None, proc.join)
            await ack_cb()
            await self._publish_status(
                payload=payload,
                text="La tarea asíncrona excedió el tiempo límite y fue cancelada.",
                metadata={"task_id": task_id, "source": "task_worker", "status": "timeout"},
            )

        except asyncio.CancelledError:
            self._logger.info("task cancelled", extra={"task_id": task_id})
            proc.terminate()
            await loop.run_in_executor(None, proc.join)
            await ack_cb()
            raise

        finally:
            self._tasks.pop(task_id, None)
            semaphore.release()

    async def _cancel_task(self, task_id: str, task: Task) -> None:
        loop = asyncio.get_running_loop()
        self._logger.info("task cancelled before reader cleanup", extra={"task_id": task_id})
        task.proc.terminate()
        await loop.run_in_executor(None, task.proc.join)
        await task.ack_cb()
        self._tasks.pop(task_id, None)
        task.semaphore.release()

    async def _publish_status(self, *, payload: dict[str, Any], text: str, metadata: dict[str, Any]) -> None:
        chat_id = payload.get("chat_id")
        if not isinstance(chat_id, int):
            return
        await self._event_bus.publish(
            OutboundEvent(
                response=ChannelResponse(
                    channel=str(payload.get("channel") or "rabbitmq"),
                    chat_id=chat_id,
                    text=text,
                    metadata=metadata,
                )
            )
        )


def _coerce_retry_after_seconds(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 30
    return max(1, value)


def _failure_text_from_result(result: dict[str, Any]) -> str:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    if metadata.get("error_code") == "rate_limit_exceeded":
        return "La tarea asíncrona falló tras reintentar por rate limit del proveedor."
    return "La tarea asíncrona falló y fue cancelada."
