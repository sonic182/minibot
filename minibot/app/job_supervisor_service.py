from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from minibot.adapters.config.schema import JobsConfig
from minibot.app.event_bus import EventBus
from minibot.core.events import (
    AgentJobCanceledEvent,
    AgentJobCompletedEvent,
    AgentJobFailedEvent,
    AgentJobTimedOutEvent,
)
from minibot.core.jobs import AgentJob, AgentJobRepository, AgentJobStatus
from minibot.shared.datetime_utils import utcnow


@dataclass
class _RunningWorker:
    job: AgentJob
    worker_id: str
    process: asyncio.subprocess.Process
    task: asyncio.Task[None]


class JobSupervisorService:
    def __init__(
        self,
        repository: AgentJobRepository,
        event_bus: EventBus,
        config: JobsConfig,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus
        self._config = config
        self._logger = logging.getLogger("minibot.jobs.supervisor")
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._workers: dict[str, _RunningWorker] = {}

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        await self._repository.requeue_stale_jobs(now=utcnow())
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None
        for running in list(self._workers.values()):
            running.process.terminate()
            with contextlib.suppress(ProcessLookupError):
                await running.process.wait()
            if not running.task.done():
                running.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await running.task
        self._workers.clear()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._reap_finished_workers()
                await self._cancel_requested_workers()
                await self._spawn_ready_jobs()
            except Exception:
                self._logger.exception("job supervisor loop failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._config.poll_interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def _spawn_ready_jobs(self) -> None:
        available_slots = self._config.max_concurrent_workers - len(self._workers)
        if available_slots <= 0:
            return
        jobs = await self._repository.lease_next_jobs(
            now=utcnow(),
            limit=available_slots,
            lease_timeout_seconds=self._config.lease_timeout_seconds,
        )
        for job in jobs:
            await self._spawn_worker(job)

    async def _spawn_worker(self, job: AgentJob) -> None:
        worker_id = uuid4().hex
        payload = {
            "job_id": job.id,
            "agent_name": job.agent_name,
            "input_payload": job.input_payload,
            "timeout_seconds": job.timeout_seconds or self._config.default_job_timeout_seconds,
        }
        env = os.environ.copy()
        env["MINIBOT_CONFIG"] = self._config_path().as_posix()
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "minibot.app.job_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await self._repository.mark_running(
            job.id,
            worker_id=worker_id,
            process_pid=process.pid,
            started_at=utcnow(),
            lease_expires_at=utcnow() + timedelta(seconds=self._config.lease_timeout_seconds),
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload).encode("utf-8"))
        process.stdin.write(b"\n")
        await process.stdin.drain()
        process.stdin.close()
        running = _RunningWorker(
            job=job,
            worker_id=worker_id,
            process=process,
            task=asyncio.create_task(self._watch_worker(job=job, worker_id=worker_id, process=process)),
        )
        self._workers[job.id] = running

    async def _watch_worker(
        self,
        *,
        job: AgentJob,
        worker_id: str,
        process: asyncio.subprocess.Process,
    ) -> None:
        timeout_seconds = max(1, int(job.timeout_seconds or self._config.default_job_timeout_seconds))
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
            error_payload = {
                "error_code": "job_timed_out",
                "error": f"job exceeded timeout of {timeout_seconds} seconds",
                "stderr": "",
            }
            await self._repository.mark_timed_out(job.id, error_payload=error_payload, finished_at=utcnow())
            await self._event_bus.publish(
                AgentJobTimedOutEvent(
                    job_id=job.id,
                    agent_name=job.agent_name,
                    channel=job.channel,
                    chat_id=job.chat_id,
                    user_id=job.user_id,
                    input_payload=job.input_payload,
                    error_payload=error_payload,
                )
            )
            return

        finished_at = utcnow()
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode not in (0, None):
            error_payload = {
                "error_code": "worker_process_failed",
                "error": f"worker exited with code {process.returncode}",
                "stderr": stderr_text,
            }
            await self._repository.mark_failed(job.id, error_payload=error_payload, finished_at=finished_at)
            await self._event_bus.publish(
                AgentJobFailedEvent(
                    job_id=job.id,
                    agent_name=job.agent_name,
                    channel=job.channel,
                    chat_id=job.chat_id,
                    user_id=job.user_id,
                    input_payload=job.input_payload,
                    error_payload=error_payload,
                )
            )
            return

        try:
            result_payload = json.loads(stdout_text) if stdout_text else {}
        except json.JSONDecodeError:
            error_payload = {
                "error_code": "worker_invalid_json",
                "error": "worker returned invalid JSON",
                "stderr": stderr_text,
                "stdout": stdout_text,
            }
            await self._repository.mark_failed(job.id, error_payload=error_payload, finished_at=finished_at)
            await self._event_bus.publish(
                AgentJobFailedEvent(
                    job_id=job.id,
                    agent_name=job.agent_name,
                    channel=job.channel,
                    chat_id=job.chat_id,
                    user_id=job.user_id,
                    input_payload=job.input_payload,
                    error_payload=error_payload,
                )
            )
            return

        if result_payload.get("ok") is True:
            await self._repository.mark_completed(job.id, result_payload=result_payload, finished_at=finished_at)
            await self._event_bus.publish(
                AgentJobCompletedEvent(
                    job_id=job.id,
                    agent_name=job.agent_name,
                    channel=job.channel,
                    chat_id=job.chat_id,
                    user_id=job.user_id,
                    input_payload=job.input_payload,
                    result_payload=result_payload,
                )
            )
            return

        error_payload = {
            "error_code": result_payload.get("error_code", "delegated_agent_failed"),
            "error": result_payload.get("error", "delegated agent job failed"),
            "result_payload": result_payload,
            "stderr": stderr_text,
        }
        await self._repository.mark_failed(job.id, error_payload=error_payload, finished_at=finished_at)
        await self._event_bus.publish(
            AgentJobFailedEvent(
                job_id=job.id,
                agent_name=job.agent_name,
                channel=job.channel,
                chat_id=job.chat_id,
                user_id=job.user_id,
                input_payload=job.input_payload,
                error_payload=error_payload,
            )
        )

    async def _reap_finished_workers(self) -> None:
        finished_ids = [job_id for job_id, worker in self._workers.items() if worker.task.done()]
        for job_id in finished_ids:
            worker = self._workers.pop(job_id)
            with contextlib.suppress(asyncio.CancelledError):
                await worker.task

    async def _cancel_requested_workers(self) -> None:
        active_jobs = await self._repository.list_jobs(
            statuses=[AgentJobStatus.QUEUED, AgentJobStatus.LEASED, AgentJobStatus.RUNNING],
            cancel_requested_only=True,
            limit=1000,
        )
        now = utcnow()
        for job in active_jobs:
            if job.cancel_requested_at is None:
                continue
            running = self._workers.get(job.id)
            if running is not None:
                running.process.terminate()
                with contextlib.suppress(ProcessLookupError):
                    await running.process.wait()
                if not running.task.done():
                    running.task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await running.task
                self._workers.pop(job.id, None)
            await self._repository.mark_canceled(
                job.id,
                error_payload={"error_code": "job_canceled", "error": "job was canceled"},
                finished_at=now,
            )
            await self._event_bus.publish(
                AgentJobCanceledEvent(
                    job_id=job.id,
                    agent_name=job.agent_name,
                    channel=job.channel,
                    chat_id=job.chat_id,
                    user_id=job.user_id,
                    input_payload=job.input_payload,
                    error_payload={"error_code": "job_canceled", "error": "job was canceled"},
                )
            )

    @staticmethod
    def _config_path() -> Path:
        from minibot.adapters.container.app_container import AppContainer

        return AppContainer.get_config_path()
