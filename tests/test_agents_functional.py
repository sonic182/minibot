from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from minibot.adapters.container.app_container import AppContainer
from minibot.adapters.messaging.console.service import ConsoleService
from minibot.app.dispatcher import Dispatcher
from tests.fixtures.llm.mock_client import ScriptedLLMClient, ScriptedLLMFactory


def _reset_container() -> None:
    AppContainer._settings = None
    AppContainer._logger = None
    AppContainer._event_bus = None
    AppContainer._memory_backend = None
    AppContainer._kv_memory_backend = None
    AppContainer._llm_client = None
    AppContainer._llm_factory = None
    AppContainer._agent_registry = None
    AppContainer._prompt_store = None
    AppContainer._prompt_service = None


def _write_config(
    *,
    tmp_path: Path,
    provider: str,
    agents_enabled: bool,
    agents_dir: Path | None = None,
    allowed_delegate_agents: list[str] | None = None,
    tool_ownership_mode: str = "shared",
    supervisor_tools_allow: list[str] | None = None,
) -> Path:
    config_path = tmp_path / "config.toml"
    sqlite_url = f"sqlite+aiosqlite:///{(tmp_path / 'test_agents_functional.db').as_posix()}"
    agents_block = ""
    if agents_enabled:
        resolved_agents_dir = agents_dir or (tmp_path / "agents")
        supervisor_lines = ["\n[agents.supervisor]"]
        if allowed_delegate_agents:
            entries = ", ".join([f'"{name}"' for name in allowed_delegate_agents])
            supervisor_lines.append(f"allowed_delegate_agents = [{entries}]")
        if supervisor_tools_allow:
            entries = ", ".join([f'"{name}"' for name in supervisor_tools_allow])
            supervisor_lines.append(f"tools_allow = [{entries}]")
        supervisor_block = "\n".join(supervisor_lines)
        agents_block = (
            "\n[agents]\n"
            "enabled = true\n"
            f'directory = "{resolved_agents_dir.as_posix()}"\n'
            "max_delegation_depth = 2\n"
            "default_timeout_seconds = 2\n"
            f'tool_ownership_mode = "{tool_ownership_mode}"\n'
            "include_agent_trace_in_metadata = true\n"
        ) + supervisor_block
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'log_level = "INFO"',
                "",
                "[channels.telegram]",
                "enabled = false",
                'bot_token = ""',
                "",
                "[llm]",
                f'provider = "{provider}"',
                'model = "gpt-4o-mini"',
                'system_prompt = "You are Minibot, a helpful assistant."',
                "",
                f"[providers.{provider}]",
                'api_key = "test-key"',
                'base_url = "http://mock.local/v1"',
                "",
                "[memory]",
                f'sqlite_url = "{sqlite_url}"',
                "",
                "[scheduler.prompts]",
                "enabled = false",
            ]
        )
        + agents_block
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_agent(
    *,
    agents_dir: Path,
    name: str,
    description: str,
    model_provider: str,
    tools_allow: list[str] | None = None,
    mcp_servers: list[str] | None = None,
) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    allow_lines = ""
    mcp_lines = ""
    if tools_allow:
        allow_lines = "tools_allow:\n" + "".join([f"  - {item}\n" for item in tools_allow])
    if mcp_servers:
        mcp_lines = "mcp_servers:\n" + "".join([f"  - {item}\n" for item in mcp_servers])
    (agents_dir / f"{name}.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "mode: agent\n"
            f"model_provider: {model_provider}\n"
            "model: gpt-4o-mini\n"
            f"{mcp_lines}"
            f"{allow_lines}"
            "---\n\n"
            f"You are {name}."
        ),
        encoding="utf-8",
    )


async def _run_single_turn(*, config_path: Path, text: str, llm_factory: ScriptedLLMFactory):
    _reset_container()
    AppContainer.configure(config_path)
    await AppContainer.initialize_storage()
    bus = AppContainer.get_event_bus()
    with (
        patch.object(AppContainer, "get_llm_factory", return_value=llm_factory),
        patch.object(AppContainer, "get_llm_client", return_value=llm_factory.create_default()),
    ):
        dispatcher = Dispatcher(bus)
        console_service = ConsoleService(bus, chat_id=999, user_id=777)
        await dispatcher.start()
        await console_service.start()
        try:
            await console_service.publish_user_message(text)
            response = await console_service.wait_for_response(3.0)
            return response.response, llm_factory
        finally:
            await console_service.stop()
            await dispatcher.stop()
            _reset_container()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_console_flow_direct_response_strategy(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir=agents_dir, name="worker", description="worker", model_provider=provider)

    default_client = ScriptedLLMClient(provider=provider)
    default_client.generate_steps = [
        {
            "payload": {
                "strategy": "direct_response",
                "agent_name": None,
                "reason": "simple question",
                "requires_tool_execution": False,
            },
            "response_id": "plan-1",
            "total_tokens": 4,
        },
        {
            "payload": {"answer": {"kind": "text", "content": "plain answer"}, "should_answer_to_user": True},
            "response_id": "direct-1",
            "total_tokens": 5,
        },
    ]

    response, _ = await _run_single_turn(
        config_path=_write_config(tmp_path=tmp_path, provider=provider, agents_enabled=True, agents_dir=agents_dir),
        text="hello",
        llm_factory=ScriptedLLMFactory(default_client=default_client),
    )

    assert response.text == "plain answer"
    assert response.metadata["routing_strategy"] == "direct_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_console_flow_supervisor_uses_tools(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir=agents_dir, name="worker", description="worker", model_provider=provider)

    default_client = ScriptedLLMClient(provider=provider)
    default_client.generate_steps = [
        {
            "payload": {
                "strategy": "supervisor_tools",
                "agent_name": None,
                "reason": "needs tool",
                "requires_tool_execution": True,
            },
            "response_id": "plan-2",
            "total_tokens": 3,
        }
    ]
    default_client.runtime_steps = [
        {
            "content": "calling tool",
            "tool_name": "calculate_expression",
            "arguments": {"expression": "2+3"},
            "call_id": "calc-1",
            "response_id": "sup-step-1",
            "total_tokens": 6,
        },
        {
            "content": '{"answer":{"kind":"text","content":"result is 5"},"should_answer_to_user":true}',
            "response_id": "sup-final",
            "total_tokens": 5,
        },
    ]

    response, _ = await _run_single_turn(
        config_path=_write_config(tmp_path=tmp_path, provider=provider, agents_enabled=True, agents_dir=agents_dir),
        text="what is 2+3?",
        llm_factory=ScriptedLLMFactory(default_client=default_client),
    )

    assert response.text == "result is 5"
    assert response.metadata["routing_strategy"] == "supervisor_tools"
    assert any("calculate_expression" in req.get("tool_names", []) for req in default_client.complete_requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_console_flow_delegate_specialist_with_exclusive_ownership(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(
        agents_dir=agents_dir,
        name="workspace_manager_agent",
        description="file specialist",
        model_provider=provider,
        tools_allow=["calculate_expression"],
    )

    default_client = ScriptedLLMClient(provider=provider)
    default_client.generate_steps = [
        {
            "payload": {
                "strategy": "delegate_agent",
                "agent_name": "workspace_manager_agent",
                "reason": "specialist",
                "requires_tool_execution": True,
            },
            "response_id": "plan-3",
            "total_tokens": 3,
        }
    ]
    default_client.runtime_steps = [
        {
            "content": '{"answer":{"kind":"text","content":"supervisor fallback"},"should_answer_to_user":true}',
            "response_id": "fallback",
            "total_tokens": 5,
        }
    ]

    worker_client = ScriptedLLMClient(provider=provider)
    worker_client.runtime_steps = [{"raise": "worker failed"}]

    response, _ = await _run_single_turn(
        config_path=_write_config(
            tmp_path=tmp_path,
            provider=provider,
            agents_enabled=True,
            agents_dir=agents_dir,
            allowed_delegate_agents=["workspace_manager_agent"],
            tool_ownership_mode="exclusive",
            supervisor_tools_allow=["current_*", "calculate_*"],
        ),
        text="do specialist math",
        llm_factory=ScriptedLLMFactory(
            default_client=default_client, agent_clients={"workspace_manager_agent": worker_client}
        ),
    )

    assert response.text == "supervisor fallback"
    assert response.metadata["routing_strategy"] == "delegate_agent"
    assert response.metadata["primary_agent"] == "supervisor"
    supervisor_tools = default_client.complete_requests[0]["tool_names"]
    assert "calculate_expression" not in supervisor_tools
    assert worker_client.complete_requests
