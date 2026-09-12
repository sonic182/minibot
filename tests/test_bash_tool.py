from __future__ import annotations

import asyncio
import os
import shlex
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from minibot.adapters.config.schema import BashToolConfig
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.bash import BashTool


def _binding(config: BashToolConfig, storage=None):
    return {item.tool.name: item for item in BashTool(config, storage=storage).bindings()}["bash"]


async def _wait_until_gone(pid: int, timeout: float = 3.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


class _FakeStorage:
    """Minimal stand-in for LocalFileStorage used in spill tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create_managed_temp_bytes_file(self, *, subdir: str, stem: str, suffix: str, content: bytes) -> dict:
        self.calls.append({"subdir": subdir, "stem": stem, "suffix": suffix, "content": content})
        return {
            "path": f"{subdir}/{stem}{suffix}",
            "absolute_path": f"/tmp/{stem}{suffix}",
            "bytes_written": len(content),
        }


@pytest.mark.asyncio
async def test_bash_runs_simple_command() -> None:
    binding = _binding(BashToolConfig())
    result = cast(
        dict[str, Any],
        await binding.handler(
            {"command": "echo hello", "timeout_seconds": None, "cwd": None, "env": None},
            ToolContext(),
        ),
    )
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "hello"
    assert result["timed_out"] is False


@pytest.mark.asyncio
async def test_bash_supports_pipelines() -> None:
    binding = _binding(BashToolConfig())
    result = cast(
        dict[str, Any],
        await binding.handler(
            {"command": "printf 'a\\nb\\n' | grep b", "timeout_seconds": None, "cwd": None, "env": None},
            ToolContext(),
        ),
    )
    assert result["ok"] is True
    assert result["stdout"].strip() == "b"


@pytest.mark.asyncio
async def test_bash_honors_timeout() -> None:
    binding = _binding(BashToolConfig(default_timeout_seconds=1, max_timeout_seconds=1))
    result = cast(
        dict[str, Any],
        await binding.handler(
            {"command": "sleep 2", "timeout_seconds": None, "cwd": None, "env": None},
            ToolContext(),
        ),
    )
    assert result["ok"] is False
    assert result["timed_out"] is True


@pytest.mark.asyncio
async def test_bash_does_not_hang_on_background_child_holding_pipes(tmp_path: Path) -> None:
    """A backgrounded child inheriting stdout/stderr must not keep the call pending forever."""
    binding = _binding(BashToolConfig(default_timeout_seconds=1, max_timeout_seconds=1))
    pid_file = tmp_path / "child.pid"
    result = cast(
        dict[str, Any],
        await asyncio.wait_for(
            binding.handler(
                {
                    "command": f"sleep 3141 & echo $! > {shlex.quote(str(pid_file))}",
                    "timeout_seconds": None,
                    "cwd": None,
                    "env": None,
                },
                ToolContext(),
            ),
            timeout=10,
        ),
    )
    assert result["timed_out"] is True
    assert result["ok"] is False

    child_pid = int(pid_file.read_text().strip())
    assert await _wait_until_gone(child_pid), f"background child {child_pid} survived the timeout"


@pytest.mark.asyncio
async def test_bash_returns_immediately_for_detached_background_command() -> None:
    binding = _binding(BashToolConfig(default_timeout_seconds=30, max_timeout_seconds=30))
    result = cast(
        dict[str, Any],
        await asyncio.wait_for(
            binding.handler(
                {
                    "command": "sleep 30 >/dev/null 2>&1 </dev/null & echo ok",
                    "timeout_seconds": None,
                    "cwd": None,
                    "env": None,
                },
                ToolContext(),
            ),
            timeout=10,
        ),
    )
    assert result["ok"] is True
    assert result["timed_out"] is False
    assert result["stdout"].strip() == "ok"


@pytest.mark.asyncio
async def test_bash_reports_non_zero_exit() -> None:
    binding = _binding(BashToolConfig())
    result = cast(
        dict[str, Any],
        await binding.handler(
            {"command": "exit 9", "timeout_seconds": None, "cwd": None, "env": None},
            ToolContext(),
        ),
    )
    assert result["ok"] is False
    assert result["exit_code"] == 9


@pytest.mark.asyncio
async def test_bash_applies_env_overrides() -> None:
    binding = _binding(BashToolConfig())
    result = cast(
        dict[str, Any],
        await binding.handler(
            {
                "command": "echo $MINIBOT_BASH_TEST_VAR",
                "timeout_seconds": None,
                "cwd": None,
                "env": {"MINIBOT_BASH_TEST_VAR": "works"},
            },
            ToolContext(),
        ),
    )
    assert result["ok"] is True
    assert result["stdout"].strip() == "works"


@pytest.mark.asyncio
async def test_bash_truncates_output_when_over_limit() -> None:
    binding = _binding(BashToolConfig(max_output_bytes=10))
    result = cast(
        dict[str, Any],
        await binding.handler(
            {
                "command": f"{shlex.quote(sys.executable)} -c \"print('x'*100)\"",
                "timeout_seconds": None,
                "cwd": None,
                "env": None,
            },
            ToolContext(),
        ),
    )
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["stdout"] + result["stderr"]) <= 10


@pytest.mark.asyncio
async def test_bash_rejects_invalid_cwd() -> None:
    binding = _binding(BashToolConfig())
    with pytest.raises(ValueError, match="cwd does not exist"):
        await binding.handler(
            {
                "command": "echo hello",
                "timeout_seconds": None,
                "cwd": "/definitely/not/there/minibot",
                "env": None,
            },
            ToolContext(),
        )


# ---------------------------------------------------------------------------
# Spill-to-managed-file tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bash_spills_large_output_to_managed_file() -> None:
    storage = _FakeStorage()
    config = BashToolConfig(spill_to_managed_file=True, spill_after_chars=10, spill_preview_chars=5)
    binding = _binding(config, storage=storage)

    result = cast(
        dict[str, Any],
        await binding.handler(
            {"command": "printf 'abcdefghijklmnopqrstuvwxyz'", "timeout_seconds": None, "cwd": None, "env": None},
            ToolContext(),
        ),
    )

    assert result["stdout_storage"] == "managed_file"
    assert result["stdout_file_absolute_path"].endswith(".txt")
    assert result["stdout_bytes_written"] > 0
    assert len(result["stdout_preview"]) <= 5
    assert "stdout_file_absolute_path" in result["stdout_notice"]
    assert len(storage.calls) == 1


@pytest.mark.asyncio
async def test_bash_spill_disabled_by_default() -> None:
    storage = _FakeStorage()
    config = BashToolConfig()  # spill_to_managed_file=False by default
    binding = _binding(config, storage=storage)

    result = cast(
        dict[str, Any],
        await binding.handler(
            {"command": "printf 'x'%.0s {1..5000}", "timeout_seconds": None, "cwd": None, "env": None},
            ToolContext(),
        ),
    )

    assert "stdout" in result
    assert "stdout_storage" not in result
    assert len(storage.calls) == 0


@pytest.mark.asyncio
async def test_bash_spill_no_storage_falls_back_to_inline() -> None:
    config = BashToolConfig(spill_to_managed_file=True, spill_after_chars=5)
    binding = _binding(config, storage=None)

    result = cast(
        dict[str, Any],
        await binding.handler(
            {"command": "printf 'abcdefghijklmnopqrstuvwxyz'", "timeout_seconds": None, "cwd": None, "env": None},
            ToolContext(),
        ),
    )

    assert "stdout" in result
    assert "stdout_storage" not in result


@pytest.mark.asyncio
async def test_bash_no_spill_when_output_under_threshold() -> None:
    storage = _FakeStorage()
    config = BashToolConfig(spill_to_managed_file=True, spill_after_chars=10000)
    binding = _binding(config, storage=storage)

    result = cast(
        dict[str, Any],
        await binding.handler(
            {"command": "echo hi", "timeout_seconds": None, "cwd": None, "env": None},
            ToolContext(),
        ),
    )

    assert "stdout" in result
    assert "stdout_storage" not in result
    assert len(storage.calls) == 0
