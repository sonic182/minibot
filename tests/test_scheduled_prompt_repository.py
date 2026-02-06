from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import ScheduledPromptsConfig
from minibot.adapters.scheduler.sqlalchemy_prompt_store import SQLAlchemyScheduledPromptStore
from minibot.core.jobs import PromptRole, ScheduledPromptCreate, ScheduledPromptStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture()
async def prompt_store(tmp_path: Path) -> SQLAlchemyScheduledPromptStore:
    db_path = tmp_path / "scheduler" / "prompts.db"
    config = ScheduledPromptsConfig(
        enabled=True,
        sqlite_url=f"sqlite+aiosqlite:///{db_path}",
        poll_interval_seconds=1,
        lease_timeout_seconds=5,
        batch_size=5,
        max_attempts=3,
        echo=False,
    )
    store = SQLAlchemyScheduledPromptStore(config)
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_create_and_complete(prompt_store: SQLAlchemyScheduledPromptStore) -> None:
    now = _utcnow()
    job = await prompt_store.create(
        ScheduledPromptCreate(
            owner_id="tenant",
            channel="telegram",
            text="remind me",
            run_at=now,
            chat_id=1,
            user_id=2,
            role=PromptRole.USER,
        )
    )
    leased = await prompt_store.lease_due_jobs(now=now + timedelta(seconds=1), limit=10, lease_timeout_seconds=30)
    assert {j.id for j in leased} == {job.id}
    assert leased[0].status == ScheduledPromptStatus.LEASED

    await prompt_store.mark_completed(job.id)
    stored = await prompt_store.get(job.id)
    assert stored is not None
    assert stored.status == ScheduledPromptStatus.COMPLETED


@pytest.mark.asyncio
async def test_retry_and_fail(prompt_store: SQLAlchemyScheduledPromptStore) -> None:
    now = _utcnow()
    job = await prompt_store.create(
        ScheduledPromptCreate(
            owner_id="tenant",
            channel="telegram",
            text="later",
            run_at=now,
            max_attempts=2,
        )
    )
    leased = await prompt_store.lease_due_jobs(now=now + timedelta(seconds=1), limit=5, lease_timeout_seconds=5)
    assert leased and leased[0].id == job.id

    retry_at = now + timedelta(minutes=5)
    await prompt_store.retry_job(job.id, retry_at, error="transient")
    updated = await prompt_store.get(job.id)
    assert updated is not None
    assert updated.status == ScheduledPromptStatus.PENDING
    assert updated.retry_count == 1
    assert updated.last_error == "transient"
    assert updated.run_at == retry_at

    await prompt_store.mark_failed(job.id, error="fatal")
    failed = await prompt_store.get(job.id)
    assert failed is not None
    assert failed.status == ScheduledPromptStatus.FAILED
    assert failed.last_error == "fatal"


@pytest.mark.asyncio
async def test_lease_respects_timeout(prompt_store: SQLAlchemyScheduledPromptStore) -> None:
    now = _utcnow()
    job = await prompt_store.create(
        ScheduledPromptCreate(
            owner_id="tenant",
            channel="telegram",
            text="ping",
            run_at=now,
        )
    )
    first = await prompt_store.lease_due_jobs(now=now + timedelta(seconds=1), limit=1, lease_timeout_seconds=1)
    assert first and first[0].id == job.id

    # Lease should be acquired again after timeout
    second = await prompt_store.lease_due_jobs(
        now=now + timedelta(seconds=5),
        limit=1,
        lease_timeout_seconds=1,
    )
    assert second and second[0].id == job.id
