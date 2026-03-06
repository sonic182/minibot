from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import JobsConfig
from minibot.adapters.jobs.sqlalchemy_job_store import SQLAlchemyAgentJobStore
from minibot.core.jobs import AgentJobCreate, AgentJobStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture()
async def job_store(tmp_path: Path) -> SQLAlchemyAgentJobStore:
    db_path = tmp_path / "jobs" / "agent_jobs.db"
    store = SQLAlchemyAgentJobStore(
        JobsConfig(
            enabled=True,
            sqlite_url=f"sqlite+aiosqlite:///{db_path}",
            poll_interval_seconds=1,
            batch_size=5,
            lease_timeout_seconds=5,
            default_job_timeout_seconds=30,
            max_concurrent_workers=2,
            stale_after_seconds=60,
            worker_start_timeout_seconds=5,
            echo=False,
        )
    )
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_lease_next_jobs_does_not_reclaim_running_jobs(job_store: SQLAlchemyAgentJobStore) -> None:
    now = _utcnow()
    job = await job_store.create_job(
        AgentJobCreate(agent_name="worker", input_payload={"task": "x"}, max_attempts=2, timeout_seconds=30)
    )
    leased = await job_store.lease_next_jobs(now=now, limit=1, lease_timeout_seconds=1)
    assert [item.id for item in leased] == [job.id]

    await job_store.mark_running(
        job.id,
        worker_id="worker-1",
        process_pid=123,
        started_at=now,
        lease_expires_at=now - timedelta(seconds=1),
    )

    second = await job_store.lease_next_jobs(now=now + timedelta(seconds=10), limit=1, lease_timeout_seconds=1)
    stored = await job_store.get_job(job.id)

    assert second == []
    assert stored is not None
    assert stored.status == AgentJobStatus.RUNNING


@pytest.mark.asyncio
async def test_requeue_stale_jobs_recovers_running_jobs_on_startup(job_store: SQLAlchemyAgentJobStore) -> None:
    now = _utcnow()
    job = await job_store.create_job(
        AgentJobCreate(agent_name="worker", input_payload={"task": "x"}, max_attempts=2, timeout_seconds=30)
    )
    leased = await job_store.lease_next_jobs(now=now, limit=1, lease_timeout_seconds=1)
    assert [item.id for item in leased] == [job.id]

    await job_store.mark_running(
        job.id,
        worker_id="worker-1",
        process_pid=123,
        started_at=now,
        lease_expires_at=now - timedelta(seconds=1),
    )

    recovered = await job_store.requeue_stale_jobs(now=now + timedelta(seconds=10))
    stored = await job_store.get_job(job.id)

    assert [item.id for item in recovered] == [job.id]
    assert stored is not None
    assert stored.status == AgentJobStatus.QUEUED
    assert stored.worker_id is None
    assert stored.process_pid is None
    assert stored.lease_expires_at is None


@pytest.mark.asyncio
async def test_list_jobs_can_filter_cancel_requested_only(job_store: SQLAlchemyAgentJobStore) -> None:
    now = _utcnow()
    first = await job_store.create_job(
        AgentJobCreate(
            agent_name="worker-a",
            owner_id="owner",
            channel="console",
            chat_id=1,
            user_id=2,
            input_payload={"task": "a"},
        )
    )
    await job_store.create_job(
        AgentJobCreate(
            agent_name="worker-b",
            owner_id="owner",
            channel="console",
            chat_id=1,
            user_id=2,
            input_payload={"task": "b"},
        )
    )

    requested = await job_store.request_cancel(first.id, requested_at=now)
    jobs = await job_store.list_jobs(
        owner_id="owner",
        channel="console",
        chat_id=1,
        user_id=2,
        statuses=[AgentJobStatus.QUEUED],
        cancel_requested_only=True,
        limit=10,
    )

    assert requested is True
    assert [job.id for job in jobs] == [first.id]

