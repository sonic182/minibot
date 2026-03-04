from __future__ import annotations

from typing import Any, cast

import pytest

from minibot.adapters.config.schema import BashToolConfig
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.bash import BashTool


def _binding(config: BashToolConfig):
    return {item.tool.name: item for item in BashTool(config).bindings()}["bash"]


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
            {"command": "python -c \"print('x'*100)\"", "timeout_seconds": None, "cwd": None, "env": None},
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
