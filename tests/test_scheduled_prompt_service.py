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
from minibot.core.jobs import PromptRole, ScheduledPromptStatus


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
