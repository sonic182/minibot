from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from minibot.adapters.config.schema import Settings
from minibot.adapters.tasks import worker
from minibot.core.agents import AgentSpec


class _PipeCapture:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.written: bytes | None = None

    @asynccontextmanager
    async def open(self):
        payload = self._payload
        pipe = self

        class _RX:
            async def readline(self) -> bytes:
                return payload

        class _TX:
            def write(self, data: bytes) -> None:
                pipe.written = data

        yield _RX(), _TX()


class _FakeFactory:
    def __init__(self, _: Settings) -> None:
        self._client = _FakeClient()

    def create_default(self) -> _FakeClient:
        return self._client

    def create_for_agent(self, _spec) -> _FakeClient:
        return self._client


class _FakeClient:
    def system_prompt(self) -> str:
        return "You are Minibot."

    def model_name(self) -> str:
        return "fake-model"

    def provider_name(self) -> str:
        return "fake-provider"


class _FakeRuntime:
    def __init__(self, **_: object) -> None:
        pass

    async def run(self, **_: object):
        return SimpleNamespace(
            payload="worker result",
            pre_response_meta=None,
            state=SimpleNamespace(messages=[]),
        )


@pytest.mark.asyncio
async def test_worker_async_writes_error_for_invalid_payload() -> None:
    pipe = _PipeCapture(b"not-json\n")

    await worker._worker_async(pipe)

    assert pipe.written is not None
    result = json.loads(pipe.written)
    assert result["error"] == "invalid task payload"


@pytest.mark.asyncio
async def test_run_agent_loop_returns_structured_success() -> None:
    settings = Settings()

    with (
        patch("minibot.adapters.tasks.worker.load_settings", return_value=settings),
        patch("minibot.adapters.tasks.worker.LLMClientFactory", _FakeFactory),
        patch("minibot.adapters.tasks.worker._build_worker_tools", return_value=[]),
        patch("minibot.adapters.tasks.worker.AgentRuntime", _FakeRuntime),
    ):
        result = await worker.run_agent_loop(
            {"task_id": "t1", "channel": "console", "prompt": "Summarize this", "chat_id": 1, "user_id": 2}
        )

    assert result["task_id"] == "t1"
    assert result["text"] == "worker result"
    assert result["metadata"]["model"] == "fake-model"
    assert result["metadata"]["provider"] == "fake-provider"


@pytest.mark.asyncio
async def test_run_agent_loop_resolves_specialist_agent() -> None:
    settings = Settings()
    specialist = AgentSpec(
        name="playwright_mcp_agent",
        description="browser specialist",
        system_prompt="You are browser specialist.",
        source_path=worker.Path("/tmp/agent.md"),
        mcp_servers=["playwright-cli"],
        tools_allow=["mcp_playwright-cli__*", "filesystem"],
    )

    with (
        patch("minibot.adapters.tasks.worker.load_settings", return_value=settings),
        patch("minibot.adapters.tasks.worker.LLMClientFactory", _FakeFactory),
        patch("minibot.adapters.tasks.worker.load_agent_specs", return_value=[specialist]),
        patch("minibot.adapters.tasks.worker._build_worker_tools", return_value=[]),
        patch("minibot.adapters.tasks.worker.AgentRuntime", _FakeRuntime),
    ):
        result = await worker.run_agent_loop(
            {
                "task_id": "t1",
                "channel": "console",
                "prompt": "browse",
                "agent_name": "playwright_mcp_agent",
                "chat_id": 1,
                "user_id": 2,
            }
        )

    assert result["metadata"]["agent_name"] == "playwright_mcp_agent"


def test_build_worker_tools_excludes_orchestration_tools() -> None:
    settings = Settings()
    settings.tools.http_client.enabled = True
    settings.tools.bash.enabled = True
    settings.tools.apply_patch.enabled = True
    settings.tools.file_storage.enabled = True
    settings.tools.grep.enabled = True

    spec = worker._build_worker_spec(system_prompt="You are Minibot.", environment_prompt_fragment="")
    bindings = worker._build_worker_tools(settings=settings, spec=spec)
    tool_names = {binding.tool.name for binding in bindings}

    assert "current_datetime" in tool_names
    assert "calculate_expression" in tool_names
    assert "python_execute" in tool_names
    assert "http_request" in tool_names
    assert "filesystem" in tool_names
    assert "grep" in tool_names
    assert "invoke_agent" not in tool_names
    assert "fetch_agent_info" not in tool_names
    assert "memory" not in tool_names
    assert "chat_history_info" not in tool_names
    assert "schedule" not in tool_names
    assert "self_insert_artifact" not in tool_names


def test_build_worker_tools_strips_recursive_delegation_tools() -> None:
    settings = Settings()
    settings.tools.http_client.enabled = True
    spec = AgentSpec(
        name="specialist",
        description="desc",
        system_prompt="prompt",
        source_path=worker.Path("/tmp/specialist.md"),
        tools_allow=["http_request", "spawn_task", "invoke_agent", "cancel_task", "list_tasks", "fetch_agent_info"],
    )

    bindings = worker._build_worker_tools(settings=settings, spec=spec)
    tool_names = {binding.tool.name for binding in bindings}

    assert "http_request" in tool_names
    assert "spawn_task" not in tool_names
    assert "invoke_agent" not in tool_names
    assert "cancel_task" not in tool_names
    assert "list_tasks" not in tool_names
    assert "fetch_agent_info" not in tool_names
