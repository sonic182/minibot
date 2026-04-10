from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minibot.adapters.tasks.manager import TaskManager
from minibot.app.event_bus import EventBus
from minibot.core.events import MessageEvent, OutboundEvent

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
            context={},
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
async def test_reader_success_publishes_message_event() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    manager = _make_manager(bus)
    pipe = _PipeSuccess({"task_id": "t1", "text": "the answer"})

    _, _, _, _, reader_task = await _spawn(manager, pipe, task_id="t1", prompt="hello")
    await asyncio.wait_for(reader_task, timeout=1.0)

    event = await asyncio.wait_for(sub._queue.get(), timeout=2.0)
    assert isinstance(event, MessageEvent)
    assert event.message.text == "the answer"
    assert event.message.channel == "console"
    assert event.message.metadata["task_id"] == "t1"
    assert event.message.metadata["source"] == "task_worker"
    assert event.message.chat_id == 1
    assert event.message.user_id == 2
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

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    with (
        patch("minibot.adapters.tasks.manager.aioduplex", side_effect=[(pipe_1, MagicMock()), (pipe_2, MagicMock())]),
        patch("minibot.adapters.tasks.manager.Process", side_effect=[fake_proc_1, fake_proc_2]),
    ):
        await manager.spawn(
            task_id="t-retry",
            channel="console",
            prompt="hello",
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
    assert isinstance(second_event, MessageEvent)
    assert second_event.message.text == "done"
    ack_cb.assert_called_once()
    nack_cb.assert_not_called()
    await sub.close()
