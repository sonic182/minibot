from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from minibot.adapters.config.schema import TasksConfig
from minibot.app.agent_registry import AgentRegistry
from minibot.app.llm_client_factory import ProviderOption
from minibot.core.agents import AgentSpec
from minibot.core.tasks import AmbiguousTaskIdError, TaskRecord, TaskRequest, TaskResult, TaskStatus
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.tasks import TaskTools
from minibot.shared.errors import ToolInputError


class _TaskManagerStub:
    def __init__(self) -> None:
        self.cancel_calls: list[str] = []
        self.cancel_result = False
        self.active_tasks: list[Any] = []

    async def cancel(self, task_id: str) -> bool:
        self.cancel_calls.append(task_id)
        return self.cancel_result

    def active(self) -> list[Any]:
        return list(self.active_tasks)


class _TaskStub:
    def __init__(self, task_id: str, channel: str, started_at: datetime) -> None:
        self.task_id = task_id
        self.channel = channel
        self.started_at = started_at


class _ProducerStub:
    def __init__(self) -> None:
        self.enqueued: list[TaskRequest] = []

    async def enqueue(self, task: TaskRequest) -> None:
        self.enqueued.append(task)


class _FailingProducer(_ProducerStub):
    async def enqueue(self, task: TaskRequest) -> None:
        raise RuntimeError("queue down")


def _build_tools(
    producer: _ProducerStub,
    task_manager: _TaskManagerStub,
    agent_registry: AgentRegistry | None = None,
    specialists_enabled: bool = True,
) -> dict[str, Any]:
    tools = TaskTools(
        cast(Any, producer),
        cast(Any, task_manager),
        agent_registry=agent_registry,
        specialists_enabled=specialists_enabled,
    )
    return {binding.tool.name: binding for binding in tools.bindings()}


@pytest.mark.asyncio
async def test_spawn_task_enqueues_request_and_reports_queued() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub())

    result = await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "agent_name": "playwright_mcp_agent", "context_json": '{"trace_id":"abc"}'},
        ToolContext(channel="console", chat_id=42, user_id=7),
    )

    assert result["status"] == "queued"
    assert result["channel"] == "console"
    assert result["chat_id"] == 42
    assert result["user_id"] == 7
    assert result["agent_name"] == "playwright_mcp_agent"

    enqueued = producer.enqueued[0]
    assert enqueued.task_id == result["task_id"]
    assert enqueued.channel == "console"
    assert enqueued.prompt == "Summarize logs"
    assert enqueued.agent_name == "playwright_mcp_agent"
    assert enqueued.context == {"trace_id": "abc"}
    assert enqueued.chat_id == 42
    assert enqueued.user_id == 7


@pytest.mark.asyncio
async def test_spawn_task_accepts_legacy_context_object() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub())

    await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "context": {"trace_id": "abc"}},
        ToolContext(channel="console", chat_id=42, user_id=7),
    )

    assert producer.enqueued[0].context == {"trace_id": "abc"}


@pytest.mark.asyncio
async def test_spawn_task_rejects_invalid_context_json() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub())

    with pytest.raises(ValueError, match="context_json must be valid JSON"):
        await bindings["spawn_task"].handler(
            {"prompt": "Summarize logs", "context_json": "not json"},
            ToolContext(channel="console"),
        )

    assert producer.enqueued == []


@pytest.mark.asyncio
async def test_spawn_task_rejects_unregistered_agent_name() -> None:
    producer = _ProducerStub()
    registry = AgentRegistry(
        [AgentSpec(name="general_agent", description="", system_prompt="", source_path=Path("agents/general.md"))]
    )
    bindings = _build_tools(producer, _TaskManagerStub(), agent_registry=registry)

    with pytest.raises(ValueError, match="agent_name 'general' is not a registered agent"):
        await bindings["spawn_task"].handler(
            {"prompt": "Summarize logs", "agent_name": "general"},
            ToolContext(channel="console"),
        )

    assert producer.enqueued == []


@pytest.mark.asyncio
async def test_spawn_task_rejects_named_agent_when_specialists_are_disabled() -> None:
    producer = _ProducerStub()
    registry = AgentRegistry(
        [AgentSpec(name="general_agent", description="", system_prompt="", source_path=Path("agents/general.md"))]
    )
    bindings = _build_tools(producer, _TaskManagerStub(), agent_registry=registry, specialists_enabled=False)

    with pytest.raises(ValueError, match=r"specialist agents are disabled"):
        await bindings["spawn_task"].handler(
            {"prompt": "Summarize logs", "agent_name": "general_agent"},
            ToolContext(channel="console"),
        )

    assert producer.enqueued == []


@pytest.mark.asyncio
async def test_spawn_task_still_accepts_a_generic_task_when_specialists_are_disabled() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub(), specialists_enabled=False)

    result = await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs"},
        ToolContext(channel="console"),
    )

    assert result["status"] == "queued"
    assert result["agent_name"] is None


@pytest.mark.asyncio
async def test_spawn_task_accepts_registered_agent_name() -> None:
    producer = _ProducerStub()
    registry = AgentRegistry(
        [AgentSpec(name="general_agent", description="", system_prompt="", source_path=Path("agents/general.md"))]
    )
    bindings = _build_tools(producer, _TaskManagerStub(), agent_registry=registry)

    result = await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "agent_name": "general_agent"},
        ToolContext(channel="console"),
    )

    assert result["agent_name"] == "general_agent"
    assert producer.enqueued[0].agent_name == "general_agent"


@pytest.mark.asyncio
async def test_spawn_task_defaults_the_timeout_to_the_specialists_own() -> None:
    producer = _ProducerStub()
    registry = AgentRegistry(
        [
            AgentSpec(
                name="slow_agent",
                description="",
                system_prompt="",
                source_path=Path("agents/slow.md"),
                timeout_seconds=120,
            )
        ]
    )
    bindings = _build_tools(producer, _TaskManagerStub(), agent_registry=registry)

    result = await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "agent_name": "slow_agent"},
        ToolContext(channel="console"),
    )

    assert result["limits"]["timeout_seconds"] == 120
    assert producer.enqueued[0].limits is not None
    assert producer.enqueued[0].limits.timeout_seconds == 120


@pytest.mark.asyncio
async def test_spawn_task_leaves_step_and_tool_call_budgets_to_config() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub())

    properties = bindings["spawn_task"].tool.parameters["properties"]
    assert "max_steps" not in properties
    assert "max_tool_calls" not in properties
    result = await bindings["spawn_task"].handler(
        {"prompt": "Browse", "max_steps": 20, "max_tool_calls": 10},
        ToolContext(channel="console"),
    )

    assert result["limits"]["max_steps"] == "unlimited"
    assert result["limits"]["max_tool_calls"] == "unlimited"


@pytest.mark.asyncio
async def test_spawn_task_payload_timeout_wins_over_the_specialists_own() -> None:
    producer = _ProducerStub()
    registry = AgentRegistry(
        [
            AgentSpec(
                name="slow_agent",
                description="",
                system_prompt="",
                source_path=Path("agents/slow.md"),
                timeout_seconds=120,
            )
        ]
    )
    bindings = _build_tools(producer, _TaskManagerStub(), agent_registry=registry)

    result = await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "agent_name": "slow_agent", "timeout_seconds": 45},
        ToolContext(channel="console"),
    )

    assert result["limits"]["timeout_seconds"] == 45


@pytest.mark.asyncio
async def test_spawn_task_clamps_a_specialist_timeout_above_the_worker_ceiling() -> None:
    producer = _ProducerStub()
    registry = AgentRegistry(
        [
            AgentSpec(
                name="greedy_agent",
                description="",
                system_prompt="",
                source_path=Path("agents/greedy.md"),
                timeout_seconds=99_999,
            )
        ]
    )
    bindings = _build_tools(producer, _TaskManagerStub(), agent_registry=registry)

    result = await bindings["spawn_task"].handler(
        {"prompt": "Summarize logs", "agent_name": "greedy_agent"},
        ToolContext(channel="console"),
    )

    assert result["limits"]["timeout_seconds"] == TasksConfig().worker_timeout_seconds


@pytest.mark.asyncio
async def test_cancel_task_returns_cancelled_flag() -> None:
    task_manager = _TaskManagerStub()
    task_manager.cancel_result = True
    bindings = _build_tools(_ProducerStub(), task_manager)

    result = await bindings["cancel_task"].handler({"task_id": "task-1"}, ToolContext())

    assert result == {"task_id": "task-1", "cancelled": True}
    assert task_manager.cancel_calls == ["task-1"]


@pytest.mark.asyncio
async def test_list_tasks_returns_active_tasks() -> None:
    task_manager = _TaskManagerStub()
    task_manager.active_tasks = [_TaskStub("task-1", "telegram", datetime.now(UTC))]
    bindings = _build_tools(_ProducerStub(), task_manager)

    result = await bindings["list_tasks"].handler({}, ToolContext())

    assert result["count"] == 1
    assert result["tasks"][0]["task_id"] == "task-1"
    assert result["tasks"][0]["channel"] == "telegram"
    assert isinstance(result["tasks"][0]["started_at"], str)


@pytest.mark.asyncio
async def test_spawn_task_carries_model_overrides_to_the_queue() -> None:
    producer = _ProducerStub()
    tools = TaskTools(
        cast(Any, producer),
        cast(Any, _TaskManagerStub()),
        providers=[ProviderOption(name="opencode_go", api_format="openai_responses", base_url=None, models=())],
    )
    bindings = {binding.tool.name: binding for binding in tools.bindings()}

    result = await bindings["spawn_task"].handler(
        {
            "prompt": "Summarize logs",
            "model_provider": "opencode_go",
            "model": "deepseek-v3.6",
            "reasoning_effort": "high",
        },
        ToolContext(channel="console"),
    )

    overrides = {"model_provider": "opencode_go", "model": "deepseek-v3.6", "reasoning_effort": "high"}
    assert result["model_overrides"] == overrides
    assert producer.enqueued[0].model_overrides == overrides


@pytest.mark.asyncio
async def test_spawn_task_continue_turn_records_the_chain_depth() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub())

    await bindings["spawn_task"].handler({"prompt": "fire and forget"}, ToolContext(channel="console"))
    continued = await bindings["spawn_task"].handler(
        {"prompt": "need the result", "continue_turn": True},
        ToolContext(channel="console", task_chain_depth=1),
    )

    assert [task.continuation_depth for task in producer.enqueued] == [None, 2]
    assert continued["continue_turn"] is True


@pytest.mark.asyncio
async def test_spawn_task_continue_turn_default_applies_when_unset_and_falls_back_at_the_limit() -> None:
    producer = _ProducerStub()
    tools = TaskTools(
        cast(Any, producer), cast(Any, _TaskManagerStub()), config=TasksConfig(continue_turn_default=True)
    )
    spawn = {binding.tool.name: binding for binding in tools.bindings()}["spawn_task"]

    defaulted = await spawn.handler({"prompt": "default"}, ToolContext(channel="console"))
    at_limit = await spawn.handler({"prompt": "too deep"}, ToolContext(channel="console", task_chain_depth=3))
    opted_out = await spawn.handler({"prompt": "direct", "continue_turn": False}, ToolContext(channel="console"))

    assert [task.continuation_depth for task in producer.enqueued] == [1, None, None]
    assert [defaulted["continue_turn"], at_limit["continue_turn"], opted_out["continue_turn"]] == [True, False, False]


@pytest.mark.asyncio
async def test_spawn_task_always_mode_ignores_the_model_choice_and_falls_back_at_the_limit() -> None:
    producer = _ProducerStub()
    tools = TaskTools(
        cast(Any, producer), cast(Any, _TaskManagerStub()), config=TasksConfig(continue_turn_mode="always")
    )
    spawn = {binding.tool.name: binding for binding in tools.bindings()}["spawn_task"]

    opted_out = await spawn.handler({"prompt": "no", "continue_turn": False}, ToolContext(channel="console"))
    unset = await spawn.handler({"prompt": "unset"}, ToolContext(channel="console"))
    at_limit = await spawn.handler(
        {"prompt": "too deep", "continue_turn": True}, ToolContext(channel="console", task_chain_depth=3)
    )

    assert [task.continuation_depth for task in producer.enqueued] == [1, 1, None]
    assert [opted_out["continue_turn"], unset["continue_turn"], at_limit["continue_turn"]] == [True, True, False]


@pytest.mark.asyncio
async def test_cancel_and_get_task_use_the_full_id_resolved_from_a_prefix() -> None:
    full_id = "a0dbc23f-1111-4000-8000-000000000001"
    record = TaskRecord(request=TaskRequest(task_id=full_id, channel="console", prompt="p"), status=TaskStatus.RUNNING)

    class _Repository:
        def __init__(self) -> None:
            self.events_calls: list[str] = []

        async def get(self, task_id: str, owner_id: str | None = None) -> TaskRecord | None:
            return record if full_id.startswith(task_id) else None

        async def events(self, task_id: str, **_kwargs: Any) -> list[dict[str, Any]]:
            self.events_calls.append(task_id)
            return []

        async def mark_cancelled(self, task_id: str) -> bool:
            return True

    manager = _TaskManagerStub()
    repository = _Repository()
    tools = TaskTools(cast(Any, _ProducerStub()), cast(Any, manager), task_repository=cast(Any, repository))
    bindings = {binding.tool.name: binding for binding in tools.bindings()}
    context = ToolContext(channel="console", owner_id="primary")

    await bindings["cancel_task"].handler({"task_id": "a0dbc23f"}, context)
    await bindings["get_task"].handler({"task_id": "a0dbc23f"}, context)

    assert manager.cancel_calls == [full_id]
    assert repository.events_calls == [full_id]


@pytest.mark.asyncio
async def test_cancel_and_get_task_report_an_ambiguous_prefix() -> None:
    class _Repository:
        async def get(self, task_id: str, owner_id: str | None = None) -> TaskRecord | None:
            raise AmbiguousTaskIdError(task_id)

    manager = _TaskManagerStub()
    tools = TaskTools(cast(Any, _ProducerStub()), cast(Any, manager), task_repository=cast(Any, _Repository()))
    bindings = {binding.tool.name: binding for binding in tools.bindings()}
    context = ToolContext(channel="console", owner_id="primary")

    cancelled = await bindings["cancel_task"].handler({"task_id": "b1c2d3e4"}, context)
    fetched = await bindings["get_task"].handler({"task_id": "b1c2d3e4"}, context)

    assert cancelled["cancelled"] is False
    assert fetched["found"] is False
    assert "ambiguous" in cancelled["reason"] and "ambiguous" in fetched["reason"]
    assert manager.cancel_calls == []


@pytest.mark.asyncio
async def test_list_tasks_includes_a_result_preview() -> None:
    record = TaskRecord(
        request=TaskRequest(task_id="t1", channel="console", prompt="p"),
        status=TaskStatus.DONE,
        result=TaskResult(text="x" * 400),
    )

    class _Repository:
        async def list(self, **_kwargs: Any) -> list[TaskRecord]:
            return [record]

    tools = TaskTools(
        cast(Any, _ProducerStub()), cast(Any, _TaskManagerStub()), task_repository=cast(Any, _Repository())
    )
    list_tasks = {binding.tool.name: binding for binding in tools.bindings()}["list_tasks"]

    result = await list_tasks.handler({}, ToolContext(channel="console", owner_id="primary"))

    assert result["tasks"][0]["result_preview"] == "x" * 300


@pytest.mark.asyncio
async def test_spawn_task_refuses_to_continue_past_the_chain_limit() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub())

    with pytest.raises(ToolInputError) as excinfo:
        await bindings["spawn_task"].handler(
            {"prompt": "one more", "continue_turn": True},
            ToolContext(channel="console", task_chain_depth=3),
        )

    assert excinfo.value.error_code == "task:continuation_limit"
    assert producer.enqueued == []


@pytest.mark.asyncio
async def test_spawn_task_refuses_continuing_tasks_past_the_per_turn_claim() -> None:
    producer = _ProducerStub()
    bindings = _build_tools(producer, _TaskManagerStub())
    claims = iter([True, True, True, False])
    context = ToolContext(channel="console", claim_task_continuation=lambda: next(claims))

    for _ in range(3):
        await bindings["spawn_task"].handler({"prompt": "fan out", "continue_turn": True}, context)
    with pytest.raises(ToolInputError) as excinfo:
        await bindings["spawn_task"].handler({"prompt": "one too many", "continue_turn": True}, context)

    assert excinfo.value.error_code == "task:continuation_limit"
    assert len(producer.enqueued) == 3


@pytest.mark.asyncio
async def test_spawn_task_releases_the_continuation_claim_when_enqueue_fails() -> None:
    bindings = _build_tools(_FailingProducer(), _TaskManagerStub())
    released: list[None] = []
    context = ToolContext(
        channel="console",
        claim_task_continuation=lambda: True,
        release_task_continuation=lambda: released.append(None),
    )

    with pytest.raises(RuntimeError, match="queue down"):
        await bindings["spawn_task"].handler({"prompt": "x", "continue_turn": True}, context)

    assert len(released) == 1


@pytest.mark.asyncio
async def test_spawn_task_rejects_a_non_boolean_continue_turn() -> None:
    bindings = _build_tools(_ProducerStub(), _TaskManagerStub())

    with pytest.raises(ToolInputError, match="continue_turn") as excinfo:
        await bindings["spawn_task"].handler(
            {"prompt": "x", "continue_turn": "yes"},
            ToolContext(channel="console"),
        )

    assert excinfo.value.error_code == "invalid_tool_arguments"


@pytest.mark.asyncio
async def test_spawn_task_rejects_provider_without_configured_credentials() -> None:
    producer = _ProducerStub()
    tools = TaskTools(
        cast(Any, producer),
        cast(Any, _TaskManagerStub()),
        providers=[ProviderOption(name="opencode_go", api_format="openai_responses", base_url=None, models=())],
    )
    bindings = {binding.tool.name: binding for binding in tools.bindings()}

    with pytest.raises(ValueError, match="opencode_go"):
        await bindings["spawn_task"].handler(
            {"prompt": "Summarize logs", "model_provider": "zai"},
            ToolContext(channel="console"),
        )

    assert producer.enqueued == []
