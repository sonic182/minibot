from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from minibot.adapters.config.schema import ScheduledPromptsConfig
from minibot.adapters.scheduler.sqlalchemy_prompt_store import SQLAlchemyScheduledPromptStore
from minibot.app.event_bus import EventBus
from minibot.app.scheduler_service import ScheduledPromptService
from minibot.core.events import MessageEvent
from minibot.core.jobs import PromptRecurrence, PromptRole, ScheduledPromptStatus


def _config(db_path: Path) -> ScheduledPromptsConfig:
    return ScheduledPromptsConfig(
        enabled=True,
        sqlite_url=f"sqlite+aiosqlite:///{db_path}",
        poll_interval_seconds=1,
        lease_timeout_seconds=5,
        batch_size=5,
        max_attempts=2,
        echo=False,
    )


@pytest_asyncio.fixture()
async def service(tmp_path: Path) -> ScheduledPromptService:
    db_path = tmp_path / "scheduler.db"
    config = _config(db_path)
    store = SQLAlchemyScheduledPromptStore(config)
    await store.initialize()
    bus = EventBus()
    svc = ScheduledPromptService(store, bus, config)
    return svc


@pytest.mark.asyncio
async def test_service_dispatches_user_prompt(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    config = _config(db_path)
    store = SQLAlchemyScheduledPromptStore(config)
    await store.initialize()
    bus = EventBus()
    svc = ScheduledPromptService(store, bus, config)
    subscription = bus.subscribe()
    job = await svc.schedule_prompt(
        owner_id="tenant",
        channel="telegram",
        text="wake up",
        run_at=datetime.now(timezone.utc),
        chat_id=123,
        user_id=456,
        role=PromptRole.USER,
    )

    await svc.run_pending()

    iterator = subscription.__aiter__()
    published = await iterator.__anext__()
    assert isinstance(published, MessageEvent)
    assert "scheduled a prompt" in published.message.text
    assert "wake up" in published.message.text
    stored = await store.get(job.id)
    assert stored is not None
    assert stored.status == ScheduledPromptStatus.COMPLETED
    await subscription.close()


@pytest.mark.asyncio
async def test_service_retries_on_publish_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    config = _config(db_path)
    store = SQLAlchemyScheduledPromptStore(config)
    await store.initialize()

    class FailingBus(EventBus):
        async def publish(self, event):  # type: ignore[override]
            raise RuntimeError("boom")

    svc = ScheduledPromptService(store, FailingBus(), config)
    job = await svc.schedule_prompt(
        owner_id="tenant",
        channel="telegram",
        text="ping",
        run_at=datetime.now(timezone.utc),
    )

    await svc.run_pending()

    stored = await store.get(job.id)
    assert stored is not None
    assert stored.status == ScheduledPromptStatus.PENDING
    assert stored.retry_count == 1
    assert stored.run_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_background_scheduler_wakes_for_near_term_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    config = ScheduledPromptsConfig(
        enabled=True,
        sqlite_url=f"sqlite+aiosqlite:///{db_path}",
        poll_interval_seconds=60,
        lease_timeout_seconds=5,
        batch_size=5,
        max_attempts=2,
        echo=False,
    )
    store = SQLAlchemyScheduledPromptStore(config)
    await store.initialize()
    bus = EventBus()
    svc = ScheduledPromptService(store, bus, config)
    subscription = bus.subscribe()
    await svc.start()

    run_at = datetime.now(timezone.utc) + timedelta(seconds=2)
    await svc.schedule_prompt(
        owner_id="tenant",
        channel="telegram",
        text="soon",
        run_at=run_at,
        chat_id=1,
        user_id=2,
    )

    async def _next_event():
        async for event in subscription:
            return event

    event = await asyncio.wait_for(_next_event(), timeout=5)
    assert isinstance(event, MessageEvent)
    assert "soon" in event.message.text

    await svc.stop()
    await subscription.close()


@pytest.mark.asyncio
async def test_service_reschedules_recurring_job_and_skips_missed_runs(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    config = _config(db_path)
    store = SQLAlchemyScheduledPromptStore(config)
    await store.initialize()
    bus = EventBus()
    svc = ScheduledPromptService(store, bus, config)

    run_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    interval_seconds = 300
    job = await svc.schedule_prompt(
        owner_id="tenant",
        channel="telegram",
        text="heartbeat",
        run_at=run_at,
        recurrence=PromptRecurrence.INTERVAL,
        recurrence_interval_seconds=interval_seconds,
    )

    await svc.run_pending()

    stored = await store.get(job.id)
    assert stored is not None
    assert stored.status == ScheduledPromptStatus.PENDING
    assert stored.recurrence == PromptRecurrence.INTERVAL
    assert stored.run_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_service_cancel_prompt_scoped_to_owner_and_chat(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    config = _config(db_path)
    store = SQLAlchemyScheduledPromptStore(config)
    await store.initialize()
    bus = EventBus()
    svc = ScheduledPromptService(store, bus, config)

    job = await svc.schedule_prompt(
        owner_id="tenant-a",
        channel="telegram",
        text="ping",
        run_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        chat_id=100,
        user_id=200,
    )

    denied = await svc.cancel_prompt(
        job_id=job.id,
        owner_id="tenant-b",
        channel="telegram",
        chat_id=100,
        user_id=200,
    )
    assert denied is None

    cancelled = await svc.cancel_prompt(
        job_id=job.id,
        owner_id="tenant-a",
        channel="telegram",
        chat_id=100,
        user_id=200,
    )
    assert cancelled is not None
    assert cancelled.status == ScheduledPromptStatus.CANCELLED


@pytest.mark.asyncio
async def test_service_delete_prompt_stops_active_job_before_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    config = _config(db_path)
    store = SQLAlchemyScheduledPromptStore(config)
    await store.initialize()
    bus = EventBus()
    svc = ScheduledPromptService(store, bus, config)

    job = await svc.schedule_prompt(
        owner_id="tenant-a",
        channel="telegram",
        text="ping",
        run_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        chat_id=100,
        user_id=200,
    )

    result = await svc.delete_prompt(
        job_id=job.id,
        owner_id="tenant-a",
        channel="telegram",
        chat_id=100,
        user_id=200,
    )
    assert result["deleted"] is True
    assert result["stopped_before_delete"] is True
    assert result["job"] is not None
    assert result["job"].status == ScheduledPromptStatus.PENDING
    assert await store.get(job.id) is None


@pytest.mark.asyncio
async def test_service_delete_prompt_deletes_terminal_job_directly(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    config = _config(db_path)
    store = SQLAlchemyScheduledPromptStore(config)
    await store.initialize()
    bus = EventBus()
    svc = ScheduledPromptService(store, bus, config)

    job = await svc.schedule_prompt(
        owner_id="tenant",
        channel="telegram",
        text="done",
        run_at=datetime.now(timezone.utc),
        chat_id=1,
        user_id=2,
    )
    await svc.run_pending()

    result = await svc.delete_prompt(
        job_id=job.id,
        owner_id="tenant",
        channel="telegram",
        chat_id=1,
        user_id=2,
    )
    assert result["deleted"] is True
    assert result["stopped_before_delete"] is False
    assert result["job"] is not None
    assert result["job"].status == ScheduledPromptStatus.COMPLETED
    assert await store.get(job.id) is None


@pytest.mark.asyncio
async def test_service_delete_prompt_respects_scope(tmp_path: Path) -> None:
    db_path = tmp_path / "scheduler.db"
    config = _config(db_path)
    store = SQLAlchemyScheduledPromptStore(config)
    await store.initialize()
    bus = EventBus()
    svc = ScheduledPromptService(store, bus, config)

    job = await svc.schedule_prompt(
        owner_id="tenant-a",
        channel="telegram",
        text="ping",
        run_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        chat_id=100,
        user_id=200,
    )

    denied = await svc.delete_prompt(
        job_id=job.id,
        owner_id="tenant-b",
        channel="telegram",
        chat_id=100,
        user_id=200,
    )
    assert denied["deleted"] is False
    assert denied["reason"] == "not_found"
    assert await store.get(job.id) is not None
