from __future__ import annotations

import asyncio
import multiprocessing
import signal
import time
from contextlib import asynccontextmanager

from minibot.adapters.tasks.worker import worker_entry


def _sighandler_noop(*_args: object) -> None:
    """Mimics asyncio's SIGTERM handler installed by daemon._graceful_shutdown."""


class _StubPipe:
    """A pipe that never delivers input, so the worker blocks as if mid-request."""

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


def test_worker_process_dies_on_terminate_despite_inherited_sigterm_handler() -> None:
    """A daemon-installed no-op SIGTERM handler must not survive the fork into the worker.

    Regression test for TaskManager.cancel()'s proc.terminate() being silently swallowed
    when the worker inherits the parent's asyncio SIGTERM handler across fork().
    """
    signal.signal(signal.SIGTERM, _sighandler_noop)
    try:
        proc = multiprocessing.Process(target=worker_entry, args=(_StubPipe(),), daemon=True)
        proc.start()
        try:
            time.sleep(0.2)  # let the child reach asyncio.run(...) and block on the stub pipe
            proc.terminate()
            proc.join(timeout=2.0)
            assert not proc.is_alive(), "worker ignored SIGTERM, inherited handler was not reset"
        finally:
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=2.0)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
