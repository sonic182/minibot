from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from minibot.adapters.config.schema import Settings
from minibot.adapters.tasks import worker
from minibot.app.response_parser import EMPTY_REPLY_FALLBACK_TEXT
from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolContext


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


class _ToolCapturingRuntime(_FakeRuntime):
    tools: list[object] = []

    def __init__(self, *, tools: list[object], **_: object) -> None:
        type(self).tools = tools


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
async def test_run_agent_loop_loads_extension_tools_for_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extension_name = "worker_extension"
    (tmp_path / f"{extension_name}.py").write_text(
        """
from llm_async.models import Tool

from minibot.llm.tools.base import ToolBinding


def register(mb):
    async def worker_greet(payload, context):
        return {"ok": True, "channel": context.channel}

    mb.add_tool(
        ToolBinding(
            tool=Tool(name="worker_greet", description="greet", parameters={}),
            handler=worker_greet,
        )
    )
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    settings = Settings.from_dict({"extensions": {"modules": [extension_name]}})

    with (
        patch("minibot.adapters.tasks.worker.load_settings", return_value=settings),
        patch("minibot.adapters.tasks.worker.LLMClientFactory", _FakeFactory),
        patch("minibot.adapters.tasks.worker.AgentRuntime", _ToolCapturingRuntime),
    ):
        await worker.run_agent_loop(
            {"task_id": "t1", "channel": "console", "prompt": "Greet Ana", "chat_id": 1, "user_id": 2}
        )

    binding = next(binding for binding in _ToolCapturingRuntime.tools if binding.tool.name == "worker_greet")
    assert await binding.handler({}, ToolContext(channel="console")) == {"ok": True, "channel": "console"}


class _EmptyCompletionRuntime:
    def __init__(self, **_: object) -> None:
        pass

    async def run(self, **_: object):
        return SimpleNamespace(
            payload="",
            pre_response_meta=None,
            state=SimpleNamespace(messages=[]),
        )


@pytest.mark.asyncio
async def test_run_agent_loop_falls_back_to_placeholder_text_on_empty_completion() -> None:
    settings = Settings()

    with (
        patch("minibot.adapters.tasks.worker.load_settings", return_value=settings),
        patch("minibot.adapters.tasks.worker.LLMClientFactory", _FakeFactory),
        patch("minibot.adapters.tasks.worker._build_worker_tools", return_value=[]),
        patch("minibot.adapters.tasks.worker.AgentRuntime", _EmptyCompletionRuntime),
    ):
        result = await worker.run_agent_loop(
            {"task_id": "t1", "channel": "console", "prompt": "Summarize this", "chat_id": 1, "user_id": 2}
        )

    assert result["task_id"] == "t1"
    assert result["text"] == EMPTY_REPLY_FALLBACK_TEXT


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
        tools_allow=["http_request", "spawn_task", "cancel_task", "list_tasks", "fetch_agent_info"],
    )

    bindings = worker._build_worker_tools(settings=settings, spec=spec)
    tool_names = {binding.tool.name for binding in bindings}

    assert "http_request" in tool_names
    assert "spawn_task" not in tool_names
    assert "cancel_task" not in tool_names
    assert "list_tasks" not in tool_names
    assert "fetch_agent_info" not in tool_names


def test_resolve_task_spec_applies_model_overrides_to_both_branches() -> None:
    settings = Settings()
    specialist = AgentSpec(
        name="general_agent",
        description="generalist",
        system_prompt="You are a generalist.",
        source_path=worker.Path("/tmp/agent.md"),
        model_provider="openai",
        model="gpt-4o-mini",
        max_new_tokens=4096,
        context_limit=128000,
    )
    overrides = {"model_provider": "opencode_go", "model": "deepseek-v3.6", "reasoning_effort": "high"}
    factory = _FakeFactory(settings)

    with patch("minibot.adapters.tasks.worker.load_agent_specs", return_value=[specialist]):
        specialist_spec = worker._resolve_task_spec(
            settings=settings,
            llm_factory=factory,
            environment_prompt_fragment="",
            task={"agent_name": "general_agent", "model_overrides": overrides},
        )
        default_spec = worker._resolve_task_spec(
            settings=settings,
            llm_factory=factory,
            environment_prompt_fragment="",
            task={"model_overrides": overrides},
        )

    for spec in (specialist_spec, default_spec):
        assert (spec.model_provider, spec.model, spec.reasoning_effort) == (
            "opencode_go",
            "deepseek-v3.6",
            "high",
        )
    # Without a ceiling in the payload the configured cap stands: this process read it from disk,
    # so unlike the daemon's copy it was never rewritten for another model.
    assert specialist_spec.max_new_tokens == 4096
    assert default_spec.name == "task_worker"


def test_resolve_task_spec_caps_at_the_lower_of_target_and_configured() -> None:
    """The daemon knows the target's ceiling; only this process still has the user's own cap."""
    settings = Settings()
    settings.llm.max_new_tokens = 8192
    specialist = AgentSpec(
        name="general_agent",
        description="generalist",
        system_prompt="You are a generalist.",
        source_path=worker.Path("/tmp/agent.md"),
        model_provider="openai",
        model="gpt-4o-mini",
        max_new_tokens=4096,
    )
    overrides = {"model_provider": "opencode_go", "model": "deepseek-v3.6"}
    factory = _FakeFactory(settings)

    with patch("minibot.adapters.tasks.worker.load_agent_specs", return_value=[specialist]):
        # Target allows more than the agent asked for: the agent's own cap wins.
        generous = worker._resolve_task_spec(
            settings=settings,
            llm_factory=factory,
            environment_prompt_fragment="",
            task={"agent_name": "general_agent", "model_overrides": overrides, "max_new_tokens": 65536},
        )
        # Target allows less: its ceiling wins, since the agent cannot exceed what the model does.
        tight = worker._resolve_task_spec(
            settings=settings,
            llm_factory=factory,
            environment_prompt_fragment="",
            task={"agent_name": "general_agent", "model_overrides": overrides, "max_new_tokens": 2048},
        )
        # No spec cap at all falls back to [llm]'s, also read from disk here.
        general = worker._resolve_task_spec(
            settings=settings,
            llm_factory=factory,
            environment_prompt_fragment="",
            task={"model_overrides": overrides, "max_new_tokens": 65536},
        )

    assert generous.max_new_tokens == 4096
    assert tight.max_new_tokens == 2048
    assert general.max_new_tokens == 8192
