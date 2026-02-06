from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from minibot.adapters.config.schema import ScheduledPromptsConfig
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent
from minibot.core.jobs import PromptRole, ScheduledPrompt, ScheduledPromptCreate, ScheduledPromptRepository


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
            now=_utcnow(),
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
    ) -> ScheduledPrompt:
        normalized_text = text.strip()
        if not normalized_text:
            raise ValueError("prompt text cannot be empty")
        if not owner_id:
            raise ValueError("owner_id is required")
        if not channel:
            raise ValueError("channel is required")
        normalized_run_at = self._normalize_datetime(run_at)
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
        )
        job = await self._repository.create(payload)
        self._logger.info(
            "scheduled prompt created",
            extra={"job_id": job.id, "run_at": job.run_at.isoformat()},
        )
        self._schedule_wake(job.run_at)
        return job

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
        delay = max((when - _utcnow()).total_seconds(), 0.0)
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
        next_run = _utcnow() + timedelta(seconds=delay)
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
        normalized = run_at
        if run_at.tzinfo is None:
            normalized = run_at.replace(tzinfo=timezone.utc)
        else:
            normalized = run_at.astimezone(timezone.utc)
        now = _utcnow()
        if normalized <= now:
            return now
        return normalized
