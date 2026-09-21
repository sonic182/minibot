from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minibot.adapters.config.schema import Settings
from minibot.adapters.tasks.manager import DelegationBudget, TaskManager, resolve_delegation_budget
from minibot.app.agent_registry import AgentRegistry
from minibot.app.event_bus import EventBus
from minibot.app.token_limits_autoconfig import prime_model_limits
from minibot.core.agents import AgentSpec
from minibot.core.events import OutboundEvent, OutboundFileEvent

# ---------------------------------------------------------------------------
# Fake pipe helpers
# ---------------------------------------------------------------------------


class _PipeSuccess:
    """Fake pipe: worker immediately returns a valid result."""

    def __init__(self, result: dict) -> None:
        self._result = result

    @asynccontextmanager
    async def open(self):
        result = self._result

        class _RX:
            async def readline(self) -> bytes:
                return json.dumps(result).encode() + b"\n"

        class _TX:
            def write(self, _data: bytes) -> None:
                pass

        yield _RX(), _TX()


class _PipeHang:
    """Fake pipe: worker never responds."""

    @asynccontextmanager
    async def open(self):
        class _RX:
            async def readline(self) -> bytes:
                await asyncio.sleep(100)
                return b""

        class _TX:
            def write(self, _data: bytes) -> None:
                pass

        yield _RX(), _TX()


class _PipeInvalidJSON:
    """Fake pipe: worker returns a non-JSON line."""

    @asynccontextmanager
    async def open(self):
        class _RX:
            async def readline(self) -> bytes:
                return b"not-json\n"

        class _TX:
            def write(self, _data: bytes) -> None:
                pass

        yield _RX(), _TX()


class _PipeWorkerError:
    def __init__(self, error: str, metadata: dict | None = None) -> None:
        self._error = error
        self._metadata = metadata or {}

    @asynccontextmanager
    async def open(self):
        error = self._error
        metadata = self._metadata

        class _RX:
            async def readline(self) -> bytes:
                return json.dumps({"task_id": "t-error", "error": error, "metadata": metadata}).encode() + b"\n"

        class _TX:
            def write(self, _data: bytes) -> None:
                pass

        yield _RX(), _TX()


class _FakeProc:
    def __init__(self) -> None:
        self.start_calls = 0
        self.join_calls = 0
        self.terminate_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def join(self) -> None:
        self.join_calls += 1

    def terminate(self) -> None:
        self.terminate_calls += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(bus: EventBus, timeout: float = 5.0) -> TaskManager:
    return TaskManager(event_bus=bus, worker_timeout_seconds=timeout)


async def _spawn(
    manager: TaskManager,
    pipe,
    task_id: str = "t1",
    prompt: str = "hello",
    channel: str = "console",
    agent_name: str | None = None,
    model_overrides: dict[str, str] | None = None,
):
    """Spawn a task with a fake pipe and return the mocked callbacks + semaphore."""
    ack_cb = AsyncMock()
    nack_cb = AsyncMock()
    sem = asyncio.Semaphore(1)
    await sem.acquire()  # simulate consumer pre-acquiring before delegating to manager

    fake_proc = _FakeProc()

    with (
        patch("minibot.adapters.tasks.manager.aioduplex", return_value=(pipe, MagicMock())),
        patch("minibot.adapters.tasks.manager.Process", return_value=fake_proc),
    ):
        await manager.spawn(
            task_id=task_id,
            channel=channel,
            prompt=prompt,
            agent_name=agent_name,
            context={},
            model_overrides=model_overrides,
            chat_id=1,
            user_id=2,
            ack_cb=ack_cb,
            nack_cb=nack_cb,
            semaphore=sem,
        )

    task = manager._tasks.get(task_id)
    if task is None:
        reader_task = asyncio.get_running_loop().create_future()
        reader_task.set_result(None)
    else:
        reader_task = task.reader_task
    return ack_cb, nack_cb, sem, fake_proc, reader_task


# ---------------------------------------------------------------------------
# Tests: success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reader_success_publishes_direct_outbound_event() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    manager = _make_manager(bus)
    pipe = _PipeSuccess({"task_id": "t1", "text": "the answer"})

    _, _, _, _, reader_task = await _spawn(manager, pipe, task_id="t1", prompt="hello")
    await asyncio.wait_for(reader_task, timeout=1.0)

    event = await asyncio.wait_for(sub._queue.get(), timeout=2.0)
    assert isinstance(event, OutboundEvent)
    assert event.response.text == "the answer"
    assert event.response.channel == "console"
    assert event.response.metadata["task_id"] == "t1"
    assert event.response.metadata["source"] == "task_worker"
    assert event.response.chat_id == 1
    await sub.close()


@pytest.mark.asyncio
async def test_reader_success_carries_render_kind_from_worker_metadata() -> None:
    # Regression: the worker resolves markdown vs. plain text through extract_answer() (see
    # worker.py), but the manager used to build ChannelResponse without a `render`, so Telegram
    # fell back to forced plain text and a task's markdown reply showed up as raw `**bold**`.
    bus = EventBus()
    sub = bus.subscribe()
    manager = _make_manager(bus)
    pipe = _PipeSuccess(
        {
            "task_id": "t1",
            "text": "# Informe\n\n**Ocupado:** sí",
            "metadata": {"render_kind": "markdown", "render_meta": {"disable_link_preview": True}},
        }
    )

    _, _, _, _, reader_task = await _spawn(manager, pipe, task_id="t1", channel="telegram")
    await asyncio.wait_for(reader_task, timeout=1.0)

    event = await asyncio.wait_for(sub._queue.get(), timeout=1.0)
    assert isinstance(event, OutboundEvent)
    assert event.response.render is not None
    assert event.response.render.kind == "markdown"
    assert event.response.render.text == "# Informe\n\n**Ocupado:** sí"
    assert event.response.render.meta == {"disable_link_preview": True}
    await sub.close()


@pytest.mark.asyncio
async def test_reader_success_without_render_kind_falls_back_to_plain_text() -> None:
    # Older worker payloads (or a legacy protocol event) carry no render_kind at all: no render
    # is attached, and the outbound sender's own None-render fallback still applies.
    bus = EventBus()
    sub = bus.subscribe()
    manager = _make_manager(bus)
    pipe = _PipeSuccess({"task_id": "t1", "text": "the answer"})

    _, _, _, _, reader_task = await _spawn(manager, pipe, task_id="t1", channel="telegram")
    await asyncio.wait_for(reader_task, timeout=1.0)

    event = await asyncio.wait_for(sub._queue.get(), timeout=1.0)
    assert isinstance(event, OutboundEvent)
    assert event.response.render is None
    await sub.close()


@pytest.mark.asyncio
async def test_reader_success_publishes_telegram_attachments_before_result() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    manager = _make_manager(bus)
    pipe = _PipeSuccess(
        {
            "task_id": "t1",
            "text": "worker result",
            "attachments": [{"path": "browser/shot.png", "type": "image/png", "caption": "shot"}],
        }
    )

    _, _, _, _, reader_task = await _spawn(manager, pipe, task_id="t1", channel="telegram")
    await asyncio.wait_for(reader_task, timeout=1.0)

    first_event = await asyncio.wait_for(sub._queue.get(), timeout=1.0)
    second_event = await asyncio.wait_for(sub._queue.get(), timeout=1.0)
    assert isinstance(first_event, OutboundFileEvent)
    assert first_event.response.file_path.endswith("data/files/browser/shot.png")
    assert isinstance(second_event, OutboundEvent)
    assert second_event.response.text == "worker result"
    await sub.close()


@pytest.mark.asyncio
async def test_reader_success_appends_attachment_paths_for_console() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    manager = _make_manager(bus)
    pipe = _PipeSuccess(
        {
            "task_id": "t1",
            "text": "worker result",
            "attachments": [{"path": "browser/shot.png", "type": "image/png"}],
        }
    )

    _, _, _, _, reader_task = await _spawn(manager, pipe, task_id="t1", channel="console")
    await asyncio.wait_for(reader_task, timeout=1.0)

    event = await asyncio.wait_for(sub._queue.get(), timeout=1.0)
    assert isinstance(event, OutboundEvent)
    assert "Artifacts:" in event.response.text
    assert "browser/shot.png" in event.response.text
    await sub.close()


@pytest.mark.asyncio
async def test_reader_success_acks_and_does_not_nack() -> None:
    bus = EventBus()
    manager = _make_manager(bus)
    pipe = _PipeSuccess({"task_id": "t1", "text": "ok"})

    ack_cb, nack_cb, sem, _, reader_task = await _spawn(manager, pipe)
    await asyncio.wait_for(reader_task, timeout=1.0)

    ack_cb.assert_called_once()
    nack_cb.assert_not_called()


@pytest.mark.asyncio
async def test_reader_success_releases_semaphore() -> None:
    bus = EventBus()
    manager = _make_manager(bus)
    pipe = _PipeSuccess({"task_id": "t1", "text": "ok"})

    _, _, sem, _, reader_task = await _spawn(manager, pipe)
    await asyncio.wait_for(reader_task, timeout=1.0)

    assert sem._value == 1


@pytest.mark.asyncio
async def test_reader_success_removes_task_from_registry() -> None:
    bus = EventBus()
    manager = _make_manager(bus)
    pipe = _PipeSuccess({"task_id": "t1", "text": "ok"})

    _, _, _, _, reader_task = await _spawn(manager, pipe)
    await asyncio.wait_for(reader_task, timeout=1.0)

    assert manager.active() == []


# ---------------------------------------------------------------------------
# Tests: timeout path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reader_timeout_nacks_and_terminates_process() -> None:
    bus = EventBus()
    manager = _make_manager(bus, timeout=0.05)

    ack_cb, nack_cb, sem, fake_proc, reader_task = await _spawn(manager, _PipeHang(), task_id="t2")
    await asyncio.wait_for(reader_task, timeout=1.0)

    ack_cb.assert_called_once()
    nack_cb.assert_not_called()
    assert fake_proc.terminate_calls == 1


@pytest.mark.asyncio
async def test_reader_timeout_releases_semaphore_and_clears_registry() -> None:
    bus = EventBus()
    manager = _make_manager(bus, timeout=0.05)

    _, _, sem, _, reader_task = await _spawn(manager, _PipeHang(), task_id="t2")
    await asyncio.wait_for(reader_task, timeout=1.0)

    assert sem._value == 1
    assert manager.active() == []


# ---------------------------------------------------------------------------
# Tests: invalid JSON from worker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reader_invalid_json_nacks() -> None:
    bus = EventBus()
    manager = _make_manager(bus)

    ack_cb, nack_cb, sem, _, reader_task = await _spawn(manager, _PipeInvalidJSON(), task_id="t3")
    await asyncio.wait_for(reader_task, timeout=1.0)

    ack_cb.assert_called_once()
    nack_cb.assert_not_called()
    assert sem._value == 1
    assert manager.active() == []


@pytest.mark.asyncio
async def test_reader_worker_error_nacks_without_publishing_event() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    manager = _make_manager(bus)

    ack_cb, nack_cb, sem, _, reader_task = await _spawn(manager, _PipeWorkerError("boom"), task_id="t-error")
    await asyncio.wait_for(reader_task, timeout=1.0)

    ack_cb.assert_called_once()
    nack_cb.assert_not_called()
    event = await asyncio.wait_for(sub._queue.get(), timeout=1.0)
    assert isinstance(event, OutboundEvent)
    assert sem._value == 1
    assert manager.active() == []
    await sub.close()


# ---------------------------------------------------------------------------
# Tests: cancel path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_nacks_and_terminates_process() -> None:
    bus = EventBus()
    manager = _make_manager(bus, timeout=10.0)

    ack_cb, nack_cb, _, fake_proc, _ = await _spawn(manager, _PipeHang(), task_id="t4")
    result = await manager.cancel("t4")
    await asyncio.sleep(0.2)

    assert result is True
    ack_cb.assert_called_once()
    nack_cb.assert_not_called()
    assert fake_proc.terminate_calls == 1


@pytest.mark.asyncio
async def test_cancel_releases_semaphore_and_clears_registry() -> None:
    bus = EventBus()
    manager = _make_manager(bus, timeout=10.0)

    _, _, sem, _, _ = await _spawn(manager, _PipeHang(), task_id="t4")
    await manager.cancel("t4")
    await asyncio.sleep(0.2)

    assert sem._value == 1
    assert manager.active() == []


@pytest.mark.asyncio
async def test_cancel_unknown_task_returns_false() -> None:
    bus = EventBus()
    manager = _make_manager(bus)

    result = await manager.cancel("nonexistent")

    assert result is False


# ---------------------------------------------------------------------------
# Tests: active()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_lists_spawned_task() -> None:
    bus = EventBus()
    manager = _make_manager(bus, timeout=10.0)

    await _spawn(manager, _PipeHang(), task_id="t5")

    tasks = manager.active()
    assert len(tasks) == 1
    assert tasks[0].task_id == "t5"

    await manager.cancel("t5")
    await asyncio.sleep(0.2)
    assert manager.active() == []


@pytest.mark.asyncio
async def test_reader_retryable_worker_error_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = EventBus()
    sub = bus.subscribe()
    manager = _make_manager(bus)
    ack_cb = AsyncMock()
    nack_cb = AsyncMock()
    sem = asyncio.Semaphore(1)
    await sem.acquire()
    fake_proc_1 = _FakeProc()
    fake_proc_2 = _FakeProc()
    pipe_1 = _PipeWorkerError(
        "HTTP 429: rate_limit_exceeded",
        metadata={"retryable": True, "retry_after_seconds": 1, "error_code": "rate_limit_exceeded"},
    )
    pipe_2 = _PipeSuccess({"task_id": "t-retry", "text": "done"})

    async def _fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("minibot.adapters.tasks.manager.asyncio.sleep", _fake_sleep)

    with (
        patch("minibot.adapters.tasks.manager.aioduplex", side_effect=[(pipe_1, MagicMock()), (pipe_2, MagicMock())]),
        patch("minibot.adapters.tasks.manager.Process", side_effect=[fake_proc_1, fake_proc_2]),
    ):
        await manager.spawn(
            task_id="t-retry",
            channel="console",
            prompt="hello",
            agent_name=None,
            context={},
            chat_id=1,
            user_id=2,
            ack_cb=ack_cb,
            nack_cb=nack_cb,
            semaphore=sem,
        )

        reader_task = manager._tasks["t-retry"].reader_task
        await asyncio.wait_for(reader_task, timeout=1.0)

        first_event = await asyncio.wait_for(sub._queue.get(), timeout=1.0)
        second_event = await asyncio.wait_for(sub._queue.get(), timeout=1.0)
        assert isinstance(first_event, OutboundEvent)
        assert "Reintentando en 1s" in first_event.response.text
        assert isinstance(second_event, OutboundEvent)
        assert second_event.response.text == "done"
    ack_cb.assert_called_once()
    nack_cb.assert_not_called()
    await sub.close()


@pytest.mark.asyncio
async def test_spawn_sends_the_compaction_threshold_to_the_worker() -> None:
    # The worker reloads agent specs from disk and never sees what token auto-config derived at
    # boot, so the daemon has to resolve the budget and ship it with the task.
    bus = EventBus()

    async def _budget(name: str | None, _overrides: dict) -> DelegationBudget:
        return DelegationBudget(compact_threshold_tokens=4321) if name == "prospector" else DelegationBudget()

    manager = TaskManager(bus, 5.0, budget_for=_budget)
    pipe = _PipeSuccess({"task_id": "t1", "text": "ok"})

    seen: list[dict] = []
    original = manager._read_worker_result

    async def _capture(mainpipe, payload, *args):
        seen.append(payload)
        return await original(mainpipe, payload, *args)

    manager._read_worker_result = _capture  # type: ignore[method-assign]
    _, _, _, _, reader_task = await _spawn(manager, pipe, task_id="t1", agent_name="prospector")
    await asyncio.wait_for(reader_task, timeout=1.0)

    assert seen[0]["compact_threshold_tokens"] == 4321


@pytest.mark.asyncio
async def test_spawn_forwards_model_overrides_to_the_budget_resolver() -> None:
    # The agent's configured window cannot describe another model, so the resolver gets the
    # overrides and answers for the model actually being run.
    bus = EventBus()
    seen_overrides: list[dict] = []

    async def _budget(_name: str | None, overrides: dict) -> DelegationBudget:
        seen_overrides.append(overrides)
        if overrides.get("model"):
            return DelegationBudget(compact_threshold_tokens=777, max_new_tokens=16384)
        return DelegationBudget(compact_threshold_tokens=4321)

    manager = TaskManager(bus, 5.0, budget_for=_budget)
    pipe = _PipeSuccess({"task_id": "t1", "text": "ok"})

    seen: list[dict] = []
    original = manager._read_worker_result

    async def _capture(mainpipe, payload, *args):
        seen.append(payload)
        return await original(mainpipe, payload, *args)

    manager._read_worker_result = _capture  # type: ignore[method-assign]
    overrides = {"model_provider": "opencode_go", "model": "deepseek-v3.6"}
    _, _, _, _, reader_task = await _spawn(
        manager, pipe, task_id="t1", agent_name="prospector", model_overrides=overrides
    )
    await asyncio.wait_for(reader_task, timeout=1.0)

    assert seen[0]["model_overrides"] == overrides
    assert seen_overrides == [overrides]
    assert seen[0]["compact_threshold_tokens"] == 777
    # The worker's cache is cold, so the cap has to travel with the task or it is lost.
    assert seen[0]["max_new_tokens"] == 16384


@pytest.mark.asyncio
async def test_spawn_works_without_a_budget_resolver() -> None:
    bus = EventBus()
    manager = _make_manager(bus)
    pipe = _PipeSuccess({"task_id": "t1", "text": "ok"})

    seen: list[dict] = []
    original = manager._read_worker_result

    async def _capture(mainpipe, payload, *args):
        seen.append(payload)
        return await original(mainpipe, payload, *args)

    manager._read_worker_result = _capture  # type: ignore[method-assign]
    _, _, _, _, reader_task = await _spawn(manager, pipe, task_id="t1")
    await asyncio.wait_for(reader_task, timeout=1.0)

    assert seen[0]["compact_threshold_tokens"] is None


def _settings_with_main_model() -> Settings:
    return Settings.from_dict(
        {
            "llm": {"provider": "openai_responses", "model": "gpt-5.6-luna", "max_new_tokens": 50000},
            "memory": {"max_history_tokens": 997500, "context_ratio_before_compact": 0.95},
        }
    )


def _inheriting_spec() -> AgentSpec:
    """A specialist whose frontmatter names no provider: it inherits the one from [llm]."""
    return AgentSpec(
        name="prospector",
        description="research specialist",
        system_prompt="research",
        source_path=Path("agents/prospector.md"),
        model_provider=None,
        model=None,
        context_limit=1_050_000,
    )


@pytest.mark.asyncio
async def test_budget_for_an_untouched_target_uses_what_boot_already_derived() -> None:
    settings = _settings_with_main_model()
    registry = AgentRegistry([_inheriting_spec()])

    specialist = await resolve_delegation_budget(registry, settings, "prospector", {})
    assert specialist.compact_threshold_tokens == 997500
    assert specialist.max_new_tokens is None

    # No agent_name is the default worker on the main model, whose budget auto-config wrote to
    # memory.max_history_tokens.
    general = await resolve_delegation_budget(registry, settings, None, {})
    assert general.compact_threshold_tokens == 997500


@pytest.mark.asyncio
async def test_budget_resolves_a_general_workers_model_override() -> None:
    """Returning the main model's threshold here would compact ~6x too late on a smaller target."""
    settings = _settings_with_main_model()
    prime_model_limits(
        "openai_responses", "deepseek-v4p1", {"catalog_provider": "fireworks-ai", "context": 163840, "output": 16384}
    )

    budget = await resolve_delegation_budget(AgentRegistry([]), settings, None, {"model": "deepseek-v4p1"})

    assert budget.compact_threshold_tokens == 155648
    assert budget.max_new_tokens == 16384


@pytest.mark.asyncio
async def test_budget_resolves_against_the_inherited_provider() -> None:
    """spec.model_provider is None here, so resolving against it alone would find nothing."""
    settings = _settings_with_main_model()
    prime_model_limits("openai_responses", "glm-5.3", {"catalog_provider": "zai", "context": 200000, "output": 32768})

    budget = await resolve_delegation_budget(
        AgentRegistry([_inheriting_spec()]), settings, "prospector", {"model": "glm-5.3"}
    )

    assert budget.compact_threshold_tokens == 190000
    assert budget.max_new_tokens == 32768


@pytest.mark.asyncio
async def test_budget_for_an_uncatalogued_target_sends_nothing_rather_than_another_models_window() -> None:
    settings = _settings_with_main_model()

    budget = await resolve_delegation_budget(
        AgentRegistry([_inheriting_spec()]), settings, "prospector", {"model": "never-seen"}
    )

    assert budget.compact_threshold_tokens is None
    assert budget.max_new_tokens is None


@pytest.mark.asyncio
async def test_budget_is_not_clipped_by_the_main_models_auto_derived_cap() -> None:
    """Boot rewrites both caps this side can reach, for the main model. Neither may leak here.

    `settings.llm.max_new_tokens` and every registered spec's cap are post-auto-config values
    describing a different model, so a target with a *larger* output limit must not be clipped to
    them. The worker combines this ceiling with the cap the user actually wrote.
    """
    settings = _settings_with_main_model()
    # What auto-config leaves behind after resolving the 1.05M/128k main model.
    settings.llm.max_new_tokens = 128000
    derived_spec = replace(_inheriting_spec(), max_new_tokens=128000)
    prime_model_limits(
        "openai_responses", "big-output", {"catalog_provider": "zai", "context": 400000, "output": 200000}
    )

    budget = await resolve_delegation_budget(
        AgentRegistry([derived_spec]), settings, "prospector", {"model": "big-output"}
    )

    assert budget.max_new_tokens == 200000
    assert budget.compact_threshold_tokens == 380000


@pytest.mark.asyncio
async def test_budget_is_resolved_before_the_execution_lease_is_claimed() -> None:
    """Resolving an uncached target downloads the catalog; the lease only covers the worker run."""
    bus = EventBus()
    order: list[str] = []

    async def _budget(_name: str | None, _overrides: dict) -> DelegationBudget:
        order.append("budget")
        return DelegationBudget(compact_threshold_tokens=4321)

    repository = MagicMock()

    async def _claim(*_args, **_kwargs) -> str:
        order.append("claim")
        return "lease-1"

    repository.claim_execution = _claim
    repository.mark_done = AsyncMock(return_value=True)
    repository.append_event = AsyncMock()
    manager = TaskManager(bus, 5.0, task_repository=repository, budget_for=_budget)

    _, _, _, _, reader_task = await _spawn(manager, _PipeSuccess({"task_id": "t1", "text": "ok"}), task_id="t1")
    await asyncio.wait_for(reader_task, timeout=1.0)

    assert order == ["budget", "claim"]
