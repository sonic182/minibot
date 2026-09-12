from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from multiprocessing import Process
from pathlib import Path
from typing import Any

from aiopipe import aioduplex

from minibot.adapters.tasks.worker import worker_entry
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelFileResponse, ChannelResponse
from minibot.core.events import OutboundEvent, OutboundFileEvent
from minibot.core.tasks import TaskLimits, TaskRepository, TaskResult, TaskStatus, TaskStopReason
from minibot.shared.utils import validate_attachments

_MAX_RETRYABLE_ATTEMPTS = 2
_SUPERVISOR_GRACE_SECONDS = 10


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
    def __init__(
        self,
        event_bus: EventBus,
        worker_timeout_seconds: float,
        task_repository: TaskRepository | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._worker_timeout_seconds = worker_timeout_seconds
        self._task_repository = task_repository
        self._tasks: dict[str, Task] = {}
        self._logger = logging.getLogger("minibot.tasks")

    async def spawn(
        self,
        *,
        task_id: str,
        channel: str,
        prompt: str,
        agent_name: str | None,
        context: dict[str, Any],
        chat_id: int | None,
        user_id: int | None,
        owner_id: str = "primary",
        limits: TaskLimits | None = None,
        ack_cb: Callable[[], Any],
        nack_cb: Callable[[], Any],
        semaphore: asyncio.Semaphore,
    ) -> None:
        resolved_limits = limits or TaskLimits(timeout_seconds=max(1, int(self._worker_timeout_seconds)))
        supervisor_timeout_seconds = limits.timeout_seconds if limits is not None else self._worker_timeout_seconds
        payload = {
            "task_id": task_id,
            "channel": channel,
            "prompt": prompt,
            "agent_name": agent_name,
            "context": context,
            "chat_id": chat_id,
            "user_id": user_id,
            "owner_id": owner_id,
            "limits": asdict(resolved_limits),
        }
        mainpipe, proc = self._start_worker_process()
        reader = asyncio.create_task(
            self._reader(task_id, mainpipe, proc, ack_cb, nack_cb, semaphore, payload, supervisor_timeout_seconds)
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
        if self._task_repository is not None:
            await self._task_repository.mark_running(task_id)
        self._logger.info(
            "task spawned",
            extra={"task_id": task_id, "timeout_seconds": resolved_limits.timeout_seconds},
        )

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

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.reader_task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task.reader_task

    async def _reader(
        self,
        task_id: str,
        mainpipe: Any,
        proc: Process,
        ack_cb: Callable[[], Any],
        nack_cb: Callable[[], Any],
        semaphore: asyncio.Semaphore,
        payload: dict[str, Any],
        supervisor_timeout_seconds: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        attempt = 1
        try:
            while True:
                result = await self._read_worker_result(mainpipe, payload, supervisor_timeout_seconds)
                await loop.run_in_executor(None, proc.join)
                metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
                if result.get("status") == TaskStatus.DONE.value:
                    attachments = validate_attachments(result.get("attachments"))
                    task_result = TaskResult(
                        text=str(result.get("text", "")),
                        attachments=attachments,
                        metadata=metadata,
                        stop_reason=TaskStopReason.COMPLETED,
                    )
                    if self._task_repository is not None:
                        await self._task_repository.mark_done(task_id, task_result)
                    await ack_cb()
                    await self._publish_attachments(payload, attachments, metadata.get("managed_files_root"))
                    await self._publish_result(payload, task_result)
                    self._logger.info("task completed", extra={"task_id": task_id, "attempts": attempt})
                    return

                retryable = bool(metadata.get("retryable")) and attempt < _MAX_RETRYABLE_ATTEMPTS
                if retryable:
                    retry_after_seconds = _coerce_retry_after_seconds(metadata.get("retry_after_seconds"))
                    if self._task_repository is not None:
                        await self._task_repository.update_progress(
                            task_id,
                            {"phase": "retrying", "attempt": attempt, "retry_after_seconds": retry_after_seconds},
                        )
                    await self._publish_status(
                        payload=payload,
                        text=f"La tarea asíncrona alcanzó un rate limit. Reintentando en {retry_after_seconds}s.",
                        metadata={
                            "task_id": task_id,
                            "source": "task_worker",
                            "status": "retrying",
                            "attempt": attempt,
                        },
                    )
                    attempt += 1
                    await asyncio.sleep(retry_after_seconds)
                    mainpipe, proc = self._start_worker_process()
                    continue

                status = _status_from_result(result)
                stop_reason = _stop_reason_from_result(result)
                error = str(result.get("error") or "task worker failed")
                if self._task_repository is not None:
                    await self._task_repository.mark_failed(task_id, error, stop_reason, status, metadata)
                await ack_cb()
                await self._publish_status(
                    payload=payload,
                    text=_failure_text(status, stop_reason),
                    metadata={
                        "task_id": task_id,
                        "source": "task_worker",
                        "status": status.value,
                        "attempts": attempt,
                    },
                )
                self._logger.warning(
                    "task failed", extra={"task_id": task_id, "status": status.value, "stop_reason": stop_reason.value}
                )
                return
        except TimeoutError:
            self._logger.warning("task supervisor timed out", extra={"task_id": task_id})
            proc.terminate()
            await loop.run_in_executor(None, proc.join)
            if self._task_repository is not None:
                await self._task_repository.mark_failed(
                    task_id,
                    "task worker exceeded the supervisor timeout",
                    TaskStopReason.TIMEOUT,
                    TaskStatus.TIMED_OUT,
                )
            await ack_cb()
            await self._publish_status(
                payload=payload,
                text="La tarea asíncrona excedió el tiempo límite y fue cancelada.",
                metadata={"task_id": task_id, "source": "task_worker", "status": TaskStatus.TIMED_OUT.value},
            )
        except asyncio.CancelledError:
            self._logger.info("task cancelled", extra={"task_id": task_id})
            proc.terminate()
            await loop.run_in_executor(None, proc.join)
            if self._task_repository is not None:
                await self._task_repository.mark_cancelled(task_id)
            await ack_cb()
            raise
        finally:
            self._tasks.pop(task_id, None)
            semaphore.release()

    async def _read_worker_result(
        self,
        mainpipe: Any,
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        grace_seconds = _SUPERVISOR_GRACE_SECONDS if timeout_seconds >= 1 else 0
        deadline = loop.time() + timeout_seconds + grace_seconds
        async with mainpipe.open() as (rx, tx):
            tx.write(json.dumps(payload).encode() + b"\n")
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                raw = await asyncio.wait_for(rx.readline(), timeout=remaining)
                if not raw:
                    return {"status": TaskStatus.FAILED.value, "error": "worker closed without a result"}
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    return {"status": TaskStatus.FAILED.value, "error": "worker returned invalid JSON"}
                if event.get("type") == "progress":
                    progress = event.get("progress")
                    if self._task_repository is not None and isinstance(progress, dict):
                        await self._task_repository.update_progress(str(payload["task_id"]), progress)
                    continue
                if event.get("type") == "result":
                    return event
                if "type" not in event:
                    return _legacy_result_event(event)
                return {"status": TaskStatus.FAILED.value, "error": "worker returned an invalid message"}

    async def _cancel_task(self, task_id: str, task: Task) -> None:
        loop = asyncio.get_running_loop()
        self._logger.info("task cancelled before reader cleanup", extra={"task_id": task_id})
        task.proc.terminate()
        await loop.run_in_executor(None, task.proc.join)
        if self._task_repository is not None:
            await self._task_repository.mark_cancelled(task_id)
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

    async def _publish_result(self, payload: dict[str, Any], result: TaskResult) -> None:
        await self._publish_status(
            payload=payload,
            text=_append_attachment_paths(
                text=result.text,
                channel=str(payload.get("channel") or "rabbitmq"),
                attachments=result.attachments,
            ),
            metadata={"task_id": payload.get("task_id"), "source": "task_worker", **result.metadata},
        )

    async def _publish_attachments(
        self,
        payload: dict[str, Any],
        attachments: list[dict[str, Any]],
        managed_files_root: Any,
    ) -> None:
        if not attachments or payload.get("channel") != "telegram" or not isinstance(payload.get("chat_id"), int):
            return
        base_dir = Path(managed_files_root if isinstance(managed_files_root, str) else "data/files").resolve()
        for attachment in attachments:
            file_path = _resolve_managed_attachment_path(base_dir, attachment["path"], self._logger)
            if file_path is None:
                continue
            await self._event_bus.publish(
                OutboundFileEvent(
                    response=ChannelFileResponse(
                        channel="telegram",
                        chat_id=payload["chat_id"],
                        file_path=str(file_path),
                        caption=attachment.get("caption"),
                        metadata={"task_id": payload.get("task_id"), "source": "task_worker"},
                    )
                )
            )


def _coerce_retry_after_seconds(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 30


def _status_from_result(result: dict[str, Any]) -> TaskStatus:
    return TaskStatus.TIMED_OUT if result.get("status") == TaskStatus.TIMED_OUT.value else TaskStatus.FAILED


def _stop_reason_from_result(result: dict[str, Any]) -> TaskStopReason:
    value = result.get("stop_reason")
    try:
        return TaskStopReason(value)
    except (TypeError, ValueError):
        return TaskStopReason.INVALID_RESULT


def _legacy_result_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize the pre-progress worker protocol during a rolling upgrade."""
    if event.get("error"):
        return {
            **event,
            "type": "result",
            "status": TaskStatus.FAILED.value,
            "stop_reason": TaskStopReason.WORKER_ERROR.value,
        }
    return {
        **event,
        "type": "result",
        "status": TaskStatus.DONE.value,
        "stop_reason": TaskStopReason.COMPLETED.value,
    }


def _failure_text(status: TaskStatus, stop_reason: TaskStopReason) -> str:
    if status is TaskStatus.TIMED_OUT or stop_reason is TaskStopReason.TIMEOUT:
        return "La tarea asíncrona excedió el tiempo límite y fue cancelada."
    if stop_reason in {TaskStopReason.MAX_STEPS, TaskStopReason.MAX_TOOL_CALLS}:
        return "La tarea asíncrona alcanzó un límite configurado antes de terminar."
    return "La tarea asíncrona falló y fue cancelada."


def _resolve_managed_attachment_path(base_dir: Path, relative_path: str, logger: logging.Logger) -> Path | None:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        logger.warning("managed attachment rejected absolute path", extra={"path": relative_path})
        return None
    resolved = (base_dir / candidate).resolve()
    if not resolved.is_relative_to(base_dir):
        logger.warning("managed attachment rejected path escape", extra={"path": relative_path})
        return None
    return resolved


def _append_attachment_paths(*, text: str, channel: str, attachments: list[dict[str, Any]]) -> str:
    if channel == "telegram" or not attachments:
        return text
    lines = [text.strip()] if text.strip() else []
    lines.append("Artifacts:")
    lines.extend(f"- {attachment['path']}" for attachment in attachments)
    return "\n".join(lines)
