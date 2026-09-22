from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosonic
import pytest
import pytest_asyncio

from minibot.adapters.config.schema import HTTPServerConfig, ScheduledPromptsConfig
from minibot.adapters.http import HttpServer, set_nav_entries
from minibot.adapters.scheduler.sqlalchemy_prompt_store import SQLAlchemyScheduledPromptStore
from minibot.app.event_bus import EventBus
from minibot.app.scheduler_service import ScheduledPromptService
from minibot.core.jobs import PromptRecurrence, ScheduledPromptCreate, ScheduledPromptStatus
from minibot.extensions.services.scheduler import _build_page

TOKEN = "s3cret"
OWNER = "primary"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _config(tmp_path) -> ScheduledPromptsConfig:
    return ScheduledPromptsConfig(enabled=True, sqlite_url=f"sqlite+aiosqlite:///{tmp_path}/prompts.db")


@pytest_asyncio.fixture()
async def store(tmp_path):
    instance = SQLAlchemyScheduledPromptStore(_config(tmp_path))
    await instance.initialize()
    yield instance


@pytest_asyncio.fixture()
async def server(store: SQLAlchemyScheduledPromptStore, tmp_path):
    # The poller is never started, so nothing leases the jobs out from under the test.
    service = ScheduledPromptService(store, EventBus(), _config(tmp_path))
    set_nav_entries([("/scheduled", "Scheduled")])
    instance = HttpServer(
        HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, auth_token=TOKEN),
        [("/scheduled", _build_page(service, OWNER), ("GET", "POST"))],
    )
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


async def _job(store: SQLAlchemyScheduledPromptStore, text: str, *, owner_id: str = OWNER, **kwargs):
    return await store.create(
        ScheduledPromptCreate(
            owner_id=owner_id,
            channel="telegram",
            chat_id=42,
            text=text,
            run_at=datetime.now(UTC) + timedelta(hours=1),
            **kwargs,
        )
    )


@pytest.mark.asyncio
async def test_lists_active_jobs_with_their_recurrence(server: HttpServer, store) -> None:
    await _job(store, "riega las plantas", recurrence=PromptRecurrence.INTERVAL, recurrence_interval_seconds=3600)

    async with aiosonic.HTTPClient() as client:
        response = await client.get(f"http://127.0.0.1:{server.port}/scheduled", headers=AUTH)
        assert response.status_code == 200
        body = await response.text()

    assert "riega las plantas" in body
    assert "every 3600s" in body
    assert "pending" in body


@pytest.mark.asyncio
async def test_terminal_jobs_only_show_under_status_all(server: HttpServer, store) -> None:
    done = await _job(store, "ya paso")
    await store.mark_completed(done.id)

    async with aiosonic.HTTPClient() as client:
        active = await (await client.get(f"http://127.0.0.1:{server.port}/scheduled", headers=AUTH)).text()
        every = await (await client.get(f"http://127.0.0.1:{server.port}/scheduled?status=all", headers=AUTH)).text()

    assert "ya paso" not in active
    assert "ya paso" in every


@pytest.mark.asyncio
async def test_search_filters_jobs_and_links_the_next_page(server: HttpServer, store) -> None:
    for index in range(51):
        await _job(store, f"Riega las plantas {index}")
    await _job(store, "paga la luz")

    async with aiosonic.HTTPClient() as client:
        first = await (await client.get(f"http://127.0.0.1:{server.port}/scheduled?q=riega", headers=AUTH)).text()
        rest = await (
            await client.get(f"http://127.0.0.1:{server.port}/scheduled?q=riega&offset=50", headers=AUTH)
        ).text()

    assert "paga la luz" not in first
    assert first.count("Riega las plantas") == 50
    assert 'href="/scheduled?q=riega&amp;offset=50" data-pager-next' in first
    assert rest.count("Riega las plantas") == 1
    assert "data-pager-next" not in rest


@pytest.mark.asyncio
async def test_cancel_marks_the_job_cancelled(server: HttpServer, store) -> None:
    job = await _job(store, "cancelame")

    async with aiosonic.HTTPClient() as client:
        response = await client.post(
            f"http://127.0.0.1:{server.port}/scheduled",
            data={"action": "cancel", "id": job.id},
            headers=AUTH,
        )
        assert response.status_code == 303

    assert (await store.get(job.id)).status is ScheduledPromptStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_ignores_a_job_owned_by_someone_else(server: HttpServer, store) -> None:
    job = await _job(store, "de otro", owner_id="somebody-else")

    async with aiosonic.HTTPClient() as client:
        response = await client.post(
            f"http://127.0.0.1:{server.port}/scheduled",
            data={"action": "cancel", "id": job.id},
            headers=AUTH,
        )
        assert response.status_code == 303

    assert (await store.get(job.id)).status is ScheduledPromptStatus.PENDING


@pytest.mark.asyncio
async def test_cancel_requires_auth(server: HttpServer, store) -> None:
    job = await _job(store, "sin credenciales")

    async with aiosonic.HTTPClient() as client:
        response = await client.post(
            f"http://127.0.0.1:{server.port}/scheduled", data={"action": "cancel", "id": job.id}
        )
        assert response.status_code == 401

    assert (await store.get(job.id)).status is ScheduledPromptStatus.PENDING
