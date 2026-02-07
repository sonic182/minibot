from __future__ import annotations

import pytest

from minibot.adapters.config.schema import PythonExecToolConfig
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.python_exec import HostPythonExecTool


def _binding_map(config: PythonExecToolConfig):
    return {binding.tool.name: binding for binding in HostPythonExecTool(config).bindings()}


@pytest.mark.asyncio
async def test_python_exec_runs_code_on_host() -> None:
    binding = _binding_map(PythonExecToolConfig())["python_execute"]
    result = await binding.handler(
        {"code": "print(8 + 9)", "stdin": None, "timeout_seconds": None},
        ToolContext(),
    )
    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "17"
    assert result["timed_out"] is False
    assert result["sandbox_mode"] == "basic"


@pytest.mark.asyncio
async def test_python_exec_honors_timeout() -> None:
    binding = _binding_map(PythonExecToolConfig(default_timeout_seconds=1, max_timeout_seconds=1))["python_execute"]
    result = await binding.handler(
        {"code": "import time\ntime.sleep(2)", "stdin": None, "timeout_seconds": None},
        ToolContext(),
    )
    assert result["ok"] is False
    assert result["timed_out"] is True


@pytest.mark.asyncio
async def test_python_exec_rejects_code_size_over_limit() -> None:
    binding = _binding_map(PythonExecToolConfig(max_code_bytes=10))["python_execute"]
    result = await binding.handler(
        {"code": "print('this is too long')", "stdin": None, "timeout_seconds": None},
        ToolContext(),
    )
    assert result["ok"] is False
    assert "code size exceeds limit" in result["error"]


@pytest.mark.asyncio
async def test_python_exec_uses_stdin() -> None:
    binding = _binding_map(PythonExecToolConfig())["python_execute"]
    result = await binding.handler(
        {
            "code": "import sys\nprint(sys.stdin.read().strip().upper())",
            "stdin": "hello",
            "timeout_seconds": None,
        },
        ToolContext(),
    )
    assert result["ok"] is True
    assert result["stdout"].strip() == "HELLO"


@pytest.mark.asyncio
async def test_python_environment_info_returns_runtime_and_packages() -> None:
    binding = _binding_map(PythonExecToolConfig())["python_environment_info"]
    result = await binding.handler(
        {"include_packages": True, "limit": 5, "name_prefix": None},
        ToolContext(),
    )
    assert result["ok"] is True
    assert isinstance(result["runtime_executable"], str)
    assert isinstance(result["python_version"], str)
    assert isinstance(result["packages"], list)
    assert len(result["packages"]) <= 5


@pytest.mark.asyncio
async def test_python_environment_info_supports_prefix_filter() -> None:
    binding = _binding_map(PythonExecToolConfig())["python_environment_info"]
    result = await binding.handler(
        {"include_packages": True, "limit": 20, "name_prefix": "pytest"},
        ToolContext(),
    )
    assert result["ok"] is True
    assert result["name_prefix"] == "pytest"
    for package in result["packages"]:
        assert package.lower().startswith("pytest")
