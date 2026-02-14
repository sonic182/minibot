from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from typing import Any

from minibot.adapters.config.schema import ScheduledPromptsConfig
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent
from minibot.core.jobs import (
    PromptRecurrence,
    PromptRole,
    ScheduledPrompt,
    ScheduledPromptCreate,
    ScheduledPromptRepository,
    ScheduledPromptStatus,
)
from minibot.shared.datetime_utils import ensure_utc, utcnow


class ScheduledPromptService:
    def __init__(
        self,
        repository: ScheduledPromptRepository,
        event_bus: EventBus,
        config: ScheduledPromptsConfig,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus
        self._config = config
        self._logger = logging.getLogger("minibot.scheduler.prompts")
        self._poll_interval = max(1, config.poll_interval_seconds)
        self._lease_timeout = config.lease_timeout_seconds
        self._batch_size = max(1, config.batch_size)
        self._max_attempts = max(1, config.max_attempts)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._wake_tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._task is not None:
            await self._task
            self._task = None
        for task in list(self._wake_tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._wake_tasks.clear()

    async def run_pending(self) -> int:
        jobs = await self._repository.lease_due_jobs(
            now=utcnow(),
            limit=self._batch_size,
            lease_timeout_seconds=self._lease_timeout,
        )
        for job in jobs:
            await self._dispatch_job(job)
        return len(jobs)

    async def schedule_prompt(
        self,
        *,
        owner_id: str,
        channel: str,
        text: str,
        run_at: datetime,
        chat_id: int | None = None,
        user_id: int | None = None,
        role: PromptRole = PromptRole.USER,
        metadata: dict[str, Any] | None = None,
        recurrence: PromptRecurrence = PromptRecurrence.NONE,
        recurrence_interval_seconds: int | None = None,
        recurrence_end_at: datetime | None = None,
    ) -> ScheduledPrompt:
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("prompt text cannot be empty")
        if not owner_id:
            raise ValueError("owner_id is required")
        if not channel:
            raise ValueError("channel is required")
        normalized_run_at = self._normalize_datetime(run_at)
        resolved_recurrence = self._resolve_recurrence(
            recurrence=recurrence,
            recurrence_interval_seconds=recurrence_interval_seconds,
            recurrence_end_at=recurrence_end_at,
        )
        recurrence_end = resolved_recurrence["recurrence_end_at"]
        if recurrence_end is not None and recurrence_end <= normalized_run_at:
            raise ValueError("recurrence_end_at must be after run_at")
        payload = ScheduledPromptCreate(
            owner_id=owner_id,
            channel=channel,
            text=normalized_text,
            run_at=normalized_run_at,
            chat_id=chat_id,
            user_id=user_id,
            role=role,
            metadata=metadata or {},
            max_attempts=self._max_attempts,
            recurrence=resolved_recurrence["recurrence"],
            recurrence_interval_seconds=resolved_recurrence["recurrence_interval_seconds"],
            recurrence_end_at=recurrence_end,
        )
        job = await self._repository.create(payload)
        self._logger.info(
            "scheduled prompt created",
            extra={"job_id": job.id, "run_at": job.run_at.isoformat()},
        )
        self._schedule_wake(job.run_at)
        return job

    async def list_prompts(
        self,
        *,
        owner_id: str,
        channel: str | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
        active_only: bool = True,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ScheduledPrompt]:
        statuses: list[ScheduledPromptStatus] | None = None
        if active_only:
            statuses = [ScheduledPromptStatus.PENDING, ScheduledPromptStatus.LEASED]
        jobs = await self._repository.list_jobs(
            owner_id=owner_id,
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            statuses=statuses,
            limit=limit,
            offset=offset,
        )
        return list(jobs)

    async def cancel_prompt(
        self,
        *,
        job_id: str,
        owner_id: str,
        channel: str | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
    ) -> ScheduledPrompt | None:
        job = await self._repository.get(job_id)
        if job is None or job.owner_id != owner_id:
            return None
        if channel is not None and job.channel != channel:
            return None
        if chat_id is not None and job.chat_id != chat_id:
            return None
        if user_id is not None and job.user_id != user_id:
            return None
        if job.status in {
            ScheduledPromptStatus.COMPLETED,
            ScheduledPromptStatus.FAILED,
            ScheduledPromptStatus.CANCELLED,
        }:
            return job
        await self._repository.mark_cancelled(job_id)
        return await self._repository.get(job_id)

    async def delete_prompt(
        self,
        *,
        job_id: str,
        owner_id: str,
        channel: str | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        job = await self._repository.get(job_id)
        if job is None:
            return {"job": None, "deleted": False, "stopped_before_delete": False, "reason": "not_found"}
        if job.owner_id != owner_id:
            return {"job": None, "deleted": False, "stopped_before_delete": False, "reason": "not_found"}
        if channel is not None and job.channel != channel:
            return {"job": None, "deleted": False, "stopped_before_delete": False, "reason": "not_found"}
        if chat_id is not None and job.chat_id != chat_id:
            return {"job": None, "deleted": False, "stopped_before_delete": False, "reason": "not_found"}
        if user_id is not None and job.user_id != user_id:
            return {"job": None, "deleted": False, "stopped_before_delete": False, "reason": "not_found"}

        stopped_before_delete = False
        if job.status in {ScheduledPromptStatus.PENDING, ScheduledPromptStatus.LEASED}:
            await self._repository.mark_cancelled(job_id)
            stopped_before_delete = True

        deleted = await self._repository.delete_job(job_id)
        return {
            "job": job,
            "deleted": deleted,
            "stopped_before_delete": stopped_before_delete,
            "reason": None if deleted else "delete_failed",
        }

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_pending()
            except Exception:
                self._logger.exception("scheduled prompt poll failed")
            await self._wait_for_next_iteration()

    async def _wait_for_next_iteration(self) -> None:
        if self._stop_event.is_set():
            return
        stop_task = asyncio.create_task(self._stop_event.wait())
        wake_task = asyncio.create_task(self._wake_event.wait())
        tasks: set[asyncio.Task[Any]] = {stop_task, wake_task}
        pending: set[asyncio.Task[Any]] = set()
        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=self._poll_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if wake_task in done and self._wake_event.is_set():
            self._wake_event.clear()

    def _schedule_wake(self, when: datetime) -> None:
        delay = max((when - utcnow()).total_seconds(), 0.0)
        task = asyncio.create_task(self._wake_after_delay(delay))
        self._wake_tasks.add(task)

        def _cleanup(finished: asyncio.Task[None]) -> None:
            self._wake_tasks.discard(finished)

        task.add_done_callback(_cleanup)

    async def _wake_after_delay(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            self._wake_event.set()
        except asyncio.CancelledError:
            raise

    async def _dispatch_job(self, job: ScheduledPrompt) -> None:
        try:
            await self._publish_prompt(job)
        except Exception as exc:
            await self._handle_dispatch_failure(job, str(exc))
            return

        next_run = self._next_run_for_recurrence(job)
        if next_run is not None:
            await self._repository.reschedule_recurring(job.id, next_run)
            self._schedule_wake(next_run)
            self._logger.info(
                "scheduled prompt recurrence rescheduled",
                extra={"job_id": job.id, "next_run_at": next_run.isoformat()},
            )
            return

        await self._repository.mark_completed(job.id)
        self._logger.info("scheduled prompt dispatched", extra={"job_id": job.id})

    async def _publish_prompt(self, job: ScheduledPrompt) -> None:
        text = self._format_scheduled_prompt(job)
        message = ChannelMessage(
            channel=job.channel,
            user_id=job.user_id,
            chat_id=job.chat_id,
            message_id=None,
            text=text,
            metadata=self._message_metadata(job),
        )
        await self._event_bus.publish(MessageEvent(message=message))

    async def _handle_dispatch_failure(self, job: ScheduledPrompt, error: str | None) -> None:
        if job.retry_count + 1 >= job.max_attempts:
            await self._repository.mark_failed(job.id, error)
            self._logger.error(
                "scheduled prompt failed permanently",
                extra={"job_id": job.id, "error": error},
            )
            return

        delay = self._retry_delay(job)
        next_run = utcnow() + timedelta(seconds=delay)
        await self._repository.retry_job(job.id, next_run, error)
        self._schedule_wake(next_run)
        self._logger.warning(
            "scheduled prompt execution failed; retrying",
            extra={"job_id": job.id, "retry_count": job.retry_count + 1, "delay_seconds": delay},
        )

    def _retry_delay(self, job: ScheduledPrompt) -> int:
        return min((job.retry_count + 1) * 30, 300)

    def _message_metadata(self, job: ScheduledPrompt) -> dict[str, Any]:
        metadata = dict(job.metadata or {})
        metadata.setdefault("scheduled_job_id", job.id)
        metadata.setdefault("scheduled", True)
        return metadata

    def _format_scheduled_prompt(self, job: ScheduledPrompt) -> str:
        instructions = job.text.strip()
        prefix = (
            "The user scheduled a prompt for this time with the following instructions. "
            "Fulfill them now and use tools if required:\n"
        )
        return f"{prefix}{instructions}"

    def _normalize_datetime(self, run_at: datetime) -> datetime:
        normalized = self._ensure_utc(run_at)
        now = utcnow()
        if normalized <= now:
            return now
        return normalized

    def _ensure_utc(self, value: datetime) -> datetime:
        return ensure_utc(value)

    def _resolve_recurrence(
        self,
        *,
        recurrence: PromptRecurrence,
        recurrence_interval_seconds: int | None,
        recurrence_end_at: datetime | None,
    ) -> dict[str, Any]:
        if recurrence == PromptRecurrence.NONE:
            if recurrence_interval_seconds is not None or recurrence_end_at is not None:
                raise ValueError("recurrence_interval_seconds/recurrence_end_at require recurrence='interval'")
            return {
                "recurrence": PromptRecurrence.NONE,
                "recurrence_interval_seconds": None,
                "recurrence_end_at": None,
            }

        if recurrence != PromptRecurrence.INTERVAL:
            raise ValueError("unsupported recurrence type")
        if recurrence_interval_seconds is None:
            raise ValueError("recurrence_interval_seconds is required for interval recurrence")
        interval = max(1, int(recurrence_interval_seconds))
        if interval < self._config.min_recurrence_interval_seconds:
            raise ValueError(f"recurrence_interval_seconds must be >= {self._config.min_recurrence_interval_seconds}")
        normalized_end_at = None
        if recurrence_end_at is not None:
            normalized_end_at = self._ensure_utc(recurrence_end_at)
        return {
            "recurrence": PromptRecurrence.INTERVAL,
            "recurrence_interval_seconds": interval,
            "recurrence_end_at": normalized_end_at,
        }

    def _next_run_for_recurrence(self, job: ScheduledPrompt) -> datetime | None:
        if job.recurrence != PromptRecurrence.INTERVAL:
            return None
        interval = job.recurrence_interval_seconds
        if interval is None or interval <= 0:
            return None

        now = utcnow()
        next_run = job.run_at
        if next_run <= now:
            elapsed_seconds = (now - job.run_at).total_seconds()
            steps = int(elapsed_seconds // interval) + 1
            next_run = job.run_at + timedelta(seconds=steps * interval)

        if job.recurrence_end_at is not None and next_run > job.recurrence_end_at:
            return None
        return next_run
