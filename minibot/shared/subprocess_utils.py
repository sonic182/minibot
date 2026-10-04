from __future__ import annotations

import asyncio
import contextlib
import os
import signal

_DRAIN_TIMEOUT_SECONDS = 2.0
_READ_CHUNK_BYTES = 65536


def truncate_subprocess_output(
    stdout_data: bytes,
    stderr_data: bytes,
    max_output_bytes: int,
) -> tuple[str, str, bool]:
    """Decode subprocess output under a combined stdout and stderr byte cap."""
    truncated = len(stdout_data) + len(stderr_data) > max_output_bytes
    if not truncated:
        return (
            stdout_data.decode("utf-8", errors="replace"),
            stderr_data.decode("utf-8", errors="replace"),
            False,
        )

    stdout_slice = stdout_data[:max_output_bytes]
    remaining = max(max_output_bytes - len(stdout_slice), 0)
    stderr_slice = stderr_data[:remaining]
    return (
        stdout_slice.decode("utf-8", errors="replace"),
        stderr_slice.decode("utf-8", errors="replace"),
        True,
    )


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


async def _read_capped(stream: asyncio.StreamReader | None, buffer: bytearray, limit: int) -> None:
    if stream is None:
        return
    while chunk := await stream.read(_READ_CHUNK_BYTES):
        room = limit - len(buffer)
        if room > 0:
            buffer.extend(chunk[:room])


async def _feed_stdin(process: asyncio.subprocess.Process, data: bytes | None) -> None:
    stdin = process.stdin
    if stdin is None:
        return
    try:
        if data:
            stdin.write(data)
            await stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        stdin.close()


async def _communicate_capped(
    process: asyncio.subprocess.Process,
    timeout: float,
    input: bytes | None,  # noqa: A002
    max_bytes: int,
) -> tuple[bytes, bytes, bool]:
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    limit = max_bytes + 1
    tasks = [
        asyncio.create_task(_read_capped(process.stdout, stdout_buffer, limit)),
        asyncio.create_task(_read_capped(process.stderr, stderr_buffer, limit)),
        asyncio.create_task(_feed_stdin(process, input)),
        asyncio.create_task(process.wait()),
    ]
    try:
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        timed_out = bool(pending)
        if timed_out:
            await kill_process_group(process)
            await asyncio.wait(pending, timeout=_DRAIN_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        await kill_process_group(process)
        raise
    finally:
        for task in tasks:
            task.cancel()
    return bytes(stdout_buffer), bytes(stderr_buffer), timed_out


async def communicate_with_timeout(
    process: asyncio.subprocess.Process,
    timeout: float,
    *,
    input: bytes | None = None,  # noqa: A002 - mirrors asyncio's communicate(input=...)
    max_bytes: int | None = None,
) -> tuple[bytes, bytes, bool]:
    """Run ``process.communicate()`` under ``timeout``, returning ``(stdout, stderr, timed_out)``.

    ``communicate()`` waits for pipe EOF, not just process exit, so any backgrounded grandchild
    holding stdout/stderr keeps it pending. On timeout the process group is killed — which closes
    those pipes — and the output is drained with a bound so this can never wait forever.

    Output is best-effort once a timeout fires because the post-kill drain is bounded.

    With ``max_bytes`` each stream is read incrementally and retains at most ``max_bytes + 1`` bytes
    (the extra byte lets ``truncate_subprocess_output`` see the overflow); the rest is read and
    discarded so a chatty child can neither block on a full pipe nor grow this process's memory.
    """
    if max_bytes is not None:
        return await _communicate_capped(process, timeout, input, max_bytes)
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
