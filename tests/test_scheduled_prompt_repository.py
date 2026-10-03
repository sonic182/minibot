from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import ScheduledPromptsConfig
from minibot.adapters.scheduler.sqlalchemy_prompt_store import SQLAlchemyScheduledPromptStore
from minibot.core.jobs import PromptRecurrence, PromptRole, ScheduledPromptCreate, ScheduledPromptStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
@pytest.mark.parametrize("legacy_schema", [False, True], ids=["fresh", "legacy"])
async def test_concurrent_first_use_initializes_shared_schema(tmp_path: Path, legacy_schema: bool) -> None:
    database_path = tmp_path / "shared.db"
    config = ScheduledPromptsConfig(enabled=True, sqlite_url=f"sqlite+aiosqlite:///{database_path}")
    if legacy_schema:
        seed_store = SQLAlchemyScheduledPromptStore(config)
        try:
            await seed_store.initialize()
        finally:
            await seed_store._engine.dispose()
        with sqlite3.connect(database_path) as connection:
            for column in (
                "recurrence",
                "recurrence_interval_seconds",
                "recurrence_cron_expression",
                "recurrence_end_at",
            ):
                connection.execute(f"ALTER TABLE scheduled_prompts DROP COLUMN {column}")

    stores = [SQLAlchemyScheduledPromptStore(config) for _ in range(8)]
    try:
        results = await asyncio.gather(
            *(store.list_jobs(owner_id="tenant") for store in stores), return_exceptions=True
        )
        assert results == [[] for _ in stores]
        job = await stores[0].create(
            ScheduledPromptCreate(
                owner_id="tenant",
                channel="telegram",
                text="repeat",
                run_at=_utcnow(),
                recurrence=PromptRecurrence.INTERVAL,
                recurrence_interval_seconds=600,
            )
        )
        loaded = await stores[-1].get(job.id)
        assert loaded is not None
        assert loaded.recurrence == PromptRecurrence.INTERVAL
        assert loaded.recurrence_interval_seconds == 600
    finally:
        await asyncio.gather(*(store._engine.dispose() for store in stores))


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


@pytest.mark.asyncio
async def test_store_persists_recurrence_and_supports_cancel_and_list(
    prompt_store: SQLAlchemyScheduledPromptStore,
) -> None:
    now = _utcnow()
    recurring = await prompt_store.create(
        ScheduledPromptCreate(
            owner_id="tenant",
            channel="telegram",
            text="repeat",
            run_at=now,
            recurrence=PromptRecurrence.INTERVAL,
            recurrence_interval_seconds=600,
        )
    )
    one_shot = await prompt_store.create(
        ScheduledPromptCreate(
            owner_id="tenant",
            channel="telegram",
            text="once",
            run_at=now + timedelta(minutes=1),
        )
    )

    loaded = await prompt_store.get(recurring.id)
    assert loaded is not None
    assert loaded.recurrence == PromptRecurrence.INTERVAL
    assert loaded.recurrence_interval_seconds == 600

    await prompt_store.mark_cancelled(one_shot.id)
    cancelled = await prompt_store.get(one_shot.id)
    assert cancelled is not None
    assert cancelled.status == ScheduledPromptStatus.CANCELLED

    jobs = await prompt_store.list_jobs(
        owner_id="tenant",
        channel="telegram",
        statuses=[ScheduledPromptStatus.PENDING],
        limit=10,
        offset=0,
    )
    assert [job.id for job in jobs] == [recurring.id]


@pytest.mark.asyncio
async def test_list_jobs_query_is_case_insensitive_and_literal(prompt_store: SQLAlchemyScheduledPromptStore) -> None:
    for text in ("Pay 100% of rent", "pay 100 of rent", "water_plants", "waterXplants", "Llamar a Ángela"):
        await prompt_store.create(
            ScheduledPromptCreate(owner_id="tenant", channel="telegram", text=text, run_at=_utcnow())
        )

    async def texts(query: str) -> list[str]:
        return sorted(job.text for job in await prompt_store.list_jobs(owner_id="tenant", query=query))

    assert await texts("PAY") == ["Pay 100% of rent", "pay 100 of rent"]
    assert await texts("100%") == ["Pay 100% of rent"]
    assert await texts("water_") == ["water_plants"]
    # Non-ASCII text matches as typed; SQLite's lower() would have folded only one side of it.
    assert await texts("Ángela") == ["Llamar a Ángela"]
    assert await texts("LLAMAR A Ángela") == ["Llamar a Ángela"]


@pytest.mark.asyncio
async def test_store_persists_cron_recurrence(prompt_store: SQLAlchemyScheduledPromptStore) -> None:
    now = _utcnow()
    job = await prompt_store.create(
        ScheduledPromptCreate(
            owner_id="tenant",
            channel="telegram",
            text="monthly report",
            run_at=now,
            recurrence=PromptRecurrence.CRON,
            recurrence_cron_expression="0 9 2 * *",
        )
    )

    loaded = await prompt_store.get(job.id)
    assert loaded is not None
    assert loaded.recurrence == PromptRecurrence.CRON
    assert loaded.recurrence_cron_expression == "0 9 2 * *"
    assert loaded.recurrence_interval_seconds is None


@pytest.mark.asyncio
async def test_delete_job_removes_record(prompt_store: SQLAlchemyScheduledPromptStore) -> None:
    now = _utcnow()
    job = await prompt_store.create(
        ScheduledPromptCreate(
            owner_id="tenant",
            channel="telegram",
            text="cleanup",
            run_at=now,
        )
    )

    deleted = await prompt_store.delete_job(job.id)
    assert deleted is True
    assert await prompt_store.get(job.id) is None

    missing = await prompt_store.delete_job(job.id)
    assert missing is False
