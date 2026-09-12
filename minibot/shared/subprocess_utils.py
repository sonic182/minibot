from __future__ import annotations

import asyncio
import contextlib
import os
import signal

_DRAIN_TIMEOUT_SECONDS = 2.0


async def kill_process_group(process: asyncio.subprocess.Process) -> None:
    """SIGKILL the whole process group, even when the direct child already exited.

    Spawned with ``start_new_session=True``, the child leads its own group, so backgrounded
    grandchildren inherit it. They can keep the stdout/stderr pipes open long after the direct
    child is gone, which is why an exited child is not a reason to skip the kill.
    """
    if os.name == "nt":
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.returncode is None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=_DRAIN_TIMEOUT_SECONDS)


async def communicate_with_timeout(
    process: asyncio.subprocess.Process,
    timeout: float,
    *,
    input: bytes | None = None,  # noqa: A002 - mirrors asyncio's communicate(input=...)
) -> tuple[bytes, bytes, bool]:
    """Run ``process.communicate()`` under ``timeout``, returning ``(stdout, stderr, timed_out)``.

    ``communicate()`` waits for pipe EOF, not just process exit, so any backgrounded grandchild
    holding stdout/stderr keeps it pending. On timeout the process group is killed — which closes
    those pipes — and the output is drained with a bound so this can never wait forever.

    Output is best-effort once a timeout fires because the post-kill drain is bounded.
    """
    try:
        stdout_data, stderr_data = await asyncio.wait_for(process.communicate(input=input), timeout=timeout)
    except TimeoutError:
        await kill_process_group(process)
        try:
            stdout_data, stderr_data = await asyncio.wait_for(process.communicate(), timeout=_DRAIN_TIMEOUT_SECONDS)
        except TimeoutError:
            stdout_data, stderr_data = b"", b""
        return stdout_data, stderr_data, True
    except asyncio.CancelledError:
        await kill_process_group(process)
        raise
    return stdout_data, stderr_data, False
