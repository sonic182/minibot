from __future__ import annotations

import asyncio
import contextlib
import os
import signal

# Bounded drain after killing the group: the pipes close as soon as the group dies, so this only
# matters for a holder that escaped the group (a `setsid` inside the command, say).
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
        await process.wait()


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

    Output is best-effort once a timeout fires: ``wait_for`` cancels the pending read, and bytes it
    had already consumed are gone with it.
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
        # The turn or task was cancelled mid-command; without this the group is orphaned.
        await kill_process_group(process)
        raise
    return stdout_data, stderr_data, False
