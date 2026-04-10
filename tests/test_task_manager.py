from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minibot.adapters.tasks.manager import TaskManager
from minibot.app.event_bus import EventBus
from minibot.core.events import MessageEvent


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(bus: EventBus, timeout: float = 5.0) -> TaskManager:
    return TaskManager(event_bus=bus, worker_timeout_seconds=timeout)


async def _spawn(manager: TaskManager, pipe, task_id: str = "t1", prompt: str = "hello"):
    """Spawn a task with a fake pipe and return the mocked callbacks + semaphore."""
    ack_cb = AsyncMock()
    nack_cb = AsyncMock()
    sem = asyncio.Semaphore(1)
    await sem.acquire()  # simulate consumer pre-acquiring before delegating to manager

    fake_proc = MagicMock()

    with (
        patch("minibot.adapters.tasks.manager.aioduplex", return_value=(pipe, MagicMock())),
        patch("minibot.adapters.tasks.manager.Process", return_value=fake_proc),
    ):
        await manager.spawn(
            task_id=task_id,
            prompt=prompt,
            context={},
            chat_id=1,
            user_id=2,
            ack_cb=ack_cb,
            nack_cb=nack_cb,
            semaphore=sem,
        )

    return ack_cb, nack_cb, sem, fake_proc


# ---------------------------------------------------------------------------
# Tests: success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reader_success_publishes_message_event() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    manager = _make_manager(bus)
    pipe = _PipeSuccess({"task_id": "t1", "text": "the answer"})

    await _spawn(manager, pipe, task_id="t1", prompt="hello")

    event = await asyncio.wait_for(sub._queue.get(), timeout=2.0)
    assert isinstance(event, MessageEvent)
    assert event.message.text == "the answer"
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

    ack_cb, nack_cb, sem, _ = await _spawn(manager, pipe)
    await asyncio.sleep(0.2)

    ack_cb.assert_called_once()
    nack_cb.assert_not_called()


@pytest.mark.asyncio
async def test_reader_success_releases_semaphore() -> None:
    bus = EventBus()
    manager = _make_manager(bus)
    pipe = _PipeSuccess({"task_id": "t1", "text": "ok"})

    _, _, sem, _ = await _spawn(manager, pipe)
    await asyncio.sleep(0.2)

    assert sem._value == 1


@pytest.mark.asyncio
async def test_reader_success_removes_task_from_registry() -> None:
    bus = EventBus()
    manager = _make_manager(bus)
    pipe = _PipeSuccess({"task_id": "t1", "text": "ok"})

    await _spawn(manager, pipe)
    await asyncio.sleep(0.2)

    assert manager.active() == []


# ---------------------------------------------------------------------------
# Tests: timeout path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reader_timeout_nacks_and_terminates_process() -> None:
    bus = EventBus()
    manager = _make_manager(bus, timeout=0.05)

    ack_cb, nack_cb, sem, fake_proc = await _spawn(manager, _PipeHang(), task_id="t2")
    await asyncio.sleep(0.5)

    nack_cb.assert_called_once()
    ack_cb.assert_not_called()
    fake_proc.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_reader_timeout_releases_semaphore_and_clears_registry() -> None:
    bus = EventBus()
    manager = _make_manager(bus, timeout=0.05)

    _, _, sem, _ = await _spawn(manager, _PipeHang(), task_id="t2")
    await asyncio.sleep(0.5)

    assert sem._value == 1
    assert manager.active() == []


# ---------------------------------------------------------------------------
# Tests: invalid JSON from worker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reader_invalid_json_nacks() -> None:
    bus = EventBus()
    manager = _make_manager(bus)

    ack_cb, nack_cb, sem, _ = await _spawn(manager, _PipeInvalidJSON(), task_id="t3")
    await asyncio.sleep(0.2)

    nack_cb.assert_called_once()
    ack_cb.assert_not_called()
    assert sem._value == 1
    assert manager.active() == []


# ---------------------------------------------------------------------------
# Tests: cancel path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_nacks_and_terminates_process() -> None:
    bus = EventBus()
    manager = _make_manager(bus, timeout=10.0)

    ack_cb, nack_cb, _, fake_proc = await _spawn(manager, _PipeHang(), task_id="t4")
    result = await manager.cancel("t4")
    await asyncio.sleep(0.2)

    assert result is True
    nack_cb.assert_called_once()
    ack_cb.assert_not_called()
    fake_proc.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_cancel_releases_semaphore_and_clears_registry() -> None:
    bus = EventBus()
    manager = _make_manager(bus, timeout=10.0)

    _, _, sem, _ = await _spawn(manager, _PipeHang(), task_id="t4")
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
