from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from minibot.adapters.config.schema import JobsConfig
from minibot.app.job_supervisor_service import JobSupervisorService
from minibot.core.jobs import AgentJob, AgentJobStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Repo:
    def __init__(self) -> None:
        self.mark_running_calls: list[dict[str, object]] = []
        self.mark_completed_calls: list[dict[str, object]] = []
        self.requeue_calls = 0

    async def requeue_stale_jobs(self, *, now: datetime):
        self.requeue_calls += 1
        return []

    async def lease_next_jobs(self, *, now: datetime, limit: int, lease_timeout_seconds: int):
        return []

    async def mark_running(
        self,
        job_id: str,
        *,
        worker_id: str,
        process_pid: int | None,
        started_at: datetime,
        lease_expires_at: datetime | None,
    ) -> None:
        self.mark_running_calls.append(
            {
                "job_id": job_id,
                "worker_id": worker_id,
                "process_pid": process_pid,
                "started_at": started_at,
                "lease_expires_at": lease_expires_at,
            }
        )

    async def mark_completed(self, job_id: str, *, result_payload, finished_at: datetime) -> None:
        self.mark_completed_calls.append(
            {"job_id": job_id, "result_payload": dict(result_payload), "finished_at": finished_at}
        )

    async def mark_failed(self, job_id: str, *, error_payload, finished_at: datetime) -> None:
        raise AssertionError("unexpected mark_failed")

    async def mark_timed_out(self, job_id: str, *, error_payload, finished_at: datetime) -> None:
        raise AssertionError("unexpected mark_timed_out")

    async def mark_canceled(self, job_id: str, *, error_payload, finished_at: datetime) -> None:
        raise AssertionError("unexpected mark_canceled")

    async def list_jobs(self, **kwargs):
        return []


class _EventBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


class _FakeStdin:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, *, sleep_seconds: float = 0.0) -> None:
        self.pid = 4321
        self.returncode = 0
        self.stdin = _FakeStdin()
        self._sleep_seconds = sleep_seconds

    async def communicate(self):
        if self._sleep_seconds:
            await asyncio.sleep(self._sleep_seconds)
        return b'{"ok": true, "result": "done"}', b""

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15


def _job() -> AgentJob:
    now = _utcnow()
    return AgentJob(
        id="job-1",
        agent_name="worker",
        status=AgentJobStatus.QUEUED,
        created_at=now,
        owner_id="owner",
        channel="console",
        chat_id=1,
        user_id=2,
        input_payload={"task": "do work"},
        timeout_seconds=30,
    )


@pytest.mark.asyncio
async def test_spawn_worker_passes_parent_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from minibot.app import job_supervisor_service as supervisor_module

    repo = _Repo()
    bus = _EventBus()
    service = JobSupervisorService(
        repo,
        bus,
        JobsConfig(
            enabled=True,
            sqlite_url=f"sqlite+aiosqlite:///{(tmp_path / 'jobs.db').as_posix()}",
            poll_interval_seconds=1,
            batch_size=5,
            lease_timeout_seconds=5,
            default_job_timeout_seconds=30,
            max_concurrent_workers=1,
            stale_after_seconds=60,
            worker_start_timeout_seconds=5,
            echo=False,
        ),
    )
    captured_env: dict[str, str] = {}
    process = _FakeProcess()
    config_path = tmp_path / "minibot.toml"

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del args
        captured_env.update(kwargs["env"])
        return process

    monkeypatch.setattr(supervisor_module.asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setattr(service, "_config_path", lambda: config_path)

    await service._spawn_worker(_job())
    await asyncio.wait_for(service._workers["job-1"].task, timeout=1.0)
    await service._reap_finished_workers()

    assert captured_env["MINIBOT_CONFIG"] == config_path.as_posix()
    assert len(repo.mark_running_calls) == 1
    lease_expires_at = repo.mark_running_calls[0]["lease_expires_at"]
    started_at = repo.mark_running_calls[0]["started_at"]
    assert isinstance(lease_expires_at, datetime)
    assert isinstance(started_at, datetime)
    assert lease_expires_at >= started_at + timedelta(seconds=30)


@pytest.mark.asyncio
async def test_watch_worker_records_finished_at_after_process_completion(tmp_path: Path) -> None:
    repo = _Repo()
    bus = _EventBus()
    service = JobSupervisorService(
        repo,
        bus,
        JobsConfig(
            enabled=True,
            sqlite_url=f"sqlite+aiosqlite:///{(tmp_path / 'jobs.db').as_posix()}",
            poll_interval_seconds=1,
            batch_size=5,
            lease_timeout_seconds=5,
            default_job_timeout_seconds=30,
            max_concurrent_workers=1,
            stale_after_seconds=60,
            worker_start_timeout_seconds=5,
            echo=False,
        ),
    )
    process = _FakeProcess(sleep_seconds=0.03)
    started_at = _utcnow()
    job = AgentJob(
        id="job-1",
        agent_name="worker",
        status=AgentJobStatus.RUNNING,
        created_at=started_at - timedelta(seconds=1),
        started_at=started_at,
        channel="console",
        chat_id=1,
        user_id=2,
        input_payload={"task": "do work"},
        timeout_seconds=30,
    )

    await service._watch_worker(job=job, worker_id="worker-1", process=process)

    assert len(repo.mark_completed_calls) == 1
    finished_at = repo.mark_completed_calls[0]["finished_at"]
    assert isinstance(finished_at, datetime)
    assert finished_at >= started_at + timedelta(milliseconds=20)
