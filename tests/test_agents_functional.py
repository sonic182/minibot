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
) -> Path:
    config_path = tmp_path / "config.toml"
    sqlite_url = f"sqlite+aiosqlite:///{(tmp_path / 'test_agents_functional.db').as_posix()}"
    agents_block = ""
    if agents_enabled:
        resolved_agents_dir = agents_dir or (tmp_path / "agents")
        supervisor_block = ""
        if allowed_delegate_agents:
            entries = ", ".join([f'"{name}"' for name in allowed_delegate_agents])
            supervisor_block = (
                "\n[agents.supervisor]\n"
                f"allowed_delegate_agents = [{entries}]\n"
            )
        agents_block = (
            "\n[agents]\n"
            "enabled = true\n"
            f"directory = \"{resolved_agents_dir.as_posix()}\"\n"
            "max_delegation_depth = 2\n"
            "default_timeout_seconds = 2\n"
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
    name: str = "worker",
    description: str = "worker agent",
    model_provider: str = "openai",
    tool_allow: list[str] | None = None,
    tool_deny: list[str] | None = None,
) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    allow_lines = ""
    deny_lines = ""
    if tool_allow:
        allow_lines = "tool_allow:\n" + "".join([f"  - {item}\n" for item in tool_allow])
    if tool_deny:
        deny_lines = "tool_deny:\n" + "".join([f"  - {item}\n" for item in tool_deny])
    (agents_dir / f"{name}.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "mode: agent\n"
            f"model_provider: {model_provider}\n"
            "model: gpt-4o-mini\n"
            "tools:\n"
            "  write: false\n"
            "  edit: false\n"
            "  bash: false\n"
            f"{allow_lines}"
            f"{deny_lines}"
            "---\n\n"
            f"You are {name}."
        ),
        encoding="utf-8",
    )


async def _run_single_turn(
    *,
    config_path: Path,
    text: str,
    llm_factory: ScriptedLLMFactory,
    chat_id: int = 999,
    user_id: int = 777,
):
    _reset_container()
    AppContainer.configure(config_path)
    await AppContainer.initialize_storage()
    bus = AppContainer.get_event_bus()
    with (
        patch.object(AppContainer, "get_llm_factory", return_value=llm_factory),
        patch.object(AppContainer, "get_llm_client", return_value=llm_factory.create_default()),
    ):
        dispatcher = Dispatcher(bus)
        console_service = ConsoleService(bus, chat_id=chat_id, user_id=user_id)
        await dispatcher.start()
        await console_service.start()
        try:
            await console_service.publish_user_message(text)
            response = await console_service.wait_for_response(3.0)
            return response.response
        finally:
            await console_service.stop()
            await dispatcher.stop()
            _reset_container()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_agents_functional_no_delegation_when_router_false(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir=agents_dir, name="worker", model_provider=provider)

    default_client = ScriptedLLMClient(provider=provider)
    default_client.generate_steps = [
        {
            "payload": {"should_delegate": False, "agent_name": None, "reason": "no specialist needed"},
            "response_id": "router-1",
            "total_tokens": 5,
        }
    ]
    default_client.runtime_steps = [
        {
            "content": '{"answer":{"kind":"text","content":"supervisor answer"},"should_answer_to_user":true}',
            "response_id": "supervisor-1",
            "total_tokens": 7,
        }
    ]
    factory = ScriptedLLMFactory(default_client=default_client)

    config_path = _write_config(tmp_path=tmp_path, provider=provider, agents_enabled=True, agents_dir=agents_dir)
    response = await _run_single_turn(config_path=config_path, text="hello", llm_factory=factory)

    assert response.text == "supervisor answer"
    assert response.metadata["primary_agent"] == "supervisor"
    assert response.metadata["delegation_fallback_used"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_agents_functional_delegated_agent_calls_tool_and_summarizes(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir=agents_dir, name="worker", model_provider=provider)

    default_client = ScriptedLLMClient(provider=provider)
    default_client.generate_steps = [
        {
            "payload": {"should_delegate": True, "agent_name": "worker", "reason": "math specialist"},
            "response_id": "router-2",
            "total_tokens": 4,
        }
    ]

    worker_client = ScriptedLLMClient(provider=provider)
    worker_client.runtime_steps = [
        {
            "tool_name": "calculate_expression",
            "arguments": {"expression": "2+3"},
            "response_id": "worker-tool-step",
            "total_tokens": 6,
        },
        {
            "content": (
                '{"answer":{"kind":"text","content":"worker summary: result is 5"},'
                '"should_answer_to_user":true}'
            ),
            "response_id": "worker-final",
            "total_tokens": 9,
        },
    ]

    factory = ScriptedLLMFactory(default_client=default_client, agent_clients={"worker": worker_client})
    config_path = _write_config(tmp_path=tmp_path, provider=provider, agents_enabled=True, agents_dir=agents_dir)
    response = await _run_single_turn(config_path=config_path, text="what is 2+3?", llm_factory=factory)

    assert response.text == "worker summary: result is 5"
    assert response.metadata["primary_agent"] == "worker"
    assert response.metadata["delegation_fallback_used"] is False
    assert isinstance(response.metadata.get("agent_trace"), list)
    assert any("calculate_expression" in req.get("tool_names", []) for req in worker_client.complete_requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_agents_functional_delegated_failure_falls_back_to_supervisor(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir=agents_dir, name="worker", model_provider=provider)

    default_client = ScriptedLLMClient(provider=provider)
    default_client.generate_steps = [
        {
            "payload": {"should_delegate": True, "agent_name": "worker", "reason": "delegating"},
            "response_id": "router-3",
            "total_tokens": 4,
        }
    ]
    default_client.runtime_steps = [
        {
            "content": (
                '{"answer":{"kind":"text","content":"supervisor fallback answer"},'
                '"should_answer_to_user":true}'
            ),
            "response_id": "supervisor-fallback",
            "total_tokens": 10,
        }
    ]

    worker_client = ScriptedLLMClient(provider=provider)
    worker_client.runtime_steps = [{"raise": "worker failed"}]

    factory = ScriptedLLMFactory(default_client=default_client, agent_clients={"worker": worker_client})
    config_path = _write_config(tmp_path=tmp_path, provider=provider, agents_enabled=True, agents_dir=agents_dir)
    response = await _run_single_turn(config_path=config_path, text="delegate then fail", llm_factory=factory)

    assert response.text == "supervisor fallback answer"
    assert response.metadata["primary_agent"] == "supervisor"
    assert response.metadata["delegation_fallback_used"] is True
    trace = response.metadata.get("agent_trace")
    assert isinstance(trace, list)
    assert any(isinstance(item, dict) and item.get("agent") == "worker" for item in trace)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_agents_functional_tool_deny_overrides_allow_in_specialist_request(
    tmp_path: Path,
    provider: str,
) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(
        agents_dir=agents_dir,
        name="policy_worker",
        model_provider=provider,
        tool_allow=["calculate_expression", "current_datetime"],
        tool_deny=["calculate_expression"],
    )

    default_client = ScriptedLLMClient(provider=provider)
    default_client.generate_steps = [
        {
            "payload": {"should_delegate": True, "agent_name": "policy_worker", "reason": "policy check"},
            "response_id": "router-4",
            "total_tokens": 3,
        }
    ]

    worker_client = ScriptedLLMClient(provider=provider)
    worker_client.runtime_steps = [
        {
            "content": '{"answer":{"kind":"text","content":"policy ok"},"should_answer_to_user":true}',
            "response_id": "policy-final",
            "total_tokens": 5,
        }
    ]

    factory = ScriptedLLMFactory(default_client=default_client, agent_clients={"policy_worker": worker_client})
    config_path = _write_config(tmp_path=tmp_path, provider=provider, agents_enabled=True, agents_dir=agents_dir)
    response = await _run_single_turn(config_path=config_path, text="check policy", llm_factory=factory)

    assert response.text == "policy ok"
    first_request = worker_client.complete_requests[0]
    assert "current_datetime" in first_request["tool_names"]
    assert "calculate_expression" not in first_request["tool_names"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_agents_functional_router_error_no_delegation_supervisor_handles(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir=agents_dir, name="worker", model_provider=provider)

    default_client = ScriptedLLMClient(provider=provider)
    default_client.generate_steps = [{"raise": "router failed"}]
    default_client.runtime_steps = [
        {
            "content": (
                '{"answer":{"kind":"text","content":"supervisor after router error"},'
                '"should_answer_to_user":true}'
            ),
            "response_id": "supervisor-after-router-error",
            "total_tokens": 9,
        }
    ]

    factory = ScriptedLLMFactory(default_client=default_client)
    config_path = _write_config(tmp_path=tmp_path, provider=provider, agents_enabled=True, agents_dir=agents_dir)
    response = await _run_single_turn(config_path=config_path, text="normal greeting", llm_factory=factory)

    assert response.text == "supervisor after router error"
    assert response.metadata["primary_agent"] == "supervisor"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_agents_functional_supervisor_allowlist_blocks_disallowed_agent(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(agents_dir=agents_dir, name="allowed_worker", model_provider=provider)
    _write_agent(agents_dir=agents_dir, name="blocked_worker", model_provider=provider)

    default_client = ScriptedLLMClient(provider=provider)
    default_client.generate_steps = [
        {
            "payload": {"should_delegate": True, "agent_name": "blocked_worker", "reason": "route anyway"},
            "response_id": "router-allowlist",
            "total_tokens": 3,
        }
    ]
    default_client.runtime_steps = [
        {
            "content": '{"answer":{"kind":"text","content":"handled by supervisor"},"should_answer_to_user":true}',
            "response_id": "supervisor-allowlist",
            "total_tokens": 6,
        }
    ]
    blocked_client = ScriptedLLMClient(provider=provider)
    blocked_client.runtime_steps = [
        {
            "content": '{"answer":{"kind":"text","content":"blocked agent response"},"should_answer_to_user":true}',
            "response_id": "blocked-1",
            "total_tokens": 6,
        }
    ]

    factory = ScriptedLLMFactory(default_client=default_client, agent_clients={"blocked_worker": blocked_client})
    config_path = _write_config(
        tmp_path=tmp_path,
        provider=provider,
        agents_enabled=True,
        agents_dir=agents_dir,
        allowed_delegate_agents=["allowed_worker"],
    )
    response = await _run_single_turn(config_path=config_path, text="delegate please", llm_factory=factory)

    assert response.text == "handled by supervisor"
    assert response.metadata["primary_agent"] == "supervisor"
    assert blocked_client.complete_requests == []
