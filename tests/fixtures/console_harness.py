from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from minibot.adapters.container.app_container import AppContainer
from minibot.adapters.messaging.console.service import ConsoleService
from minibot.app.dispatcher import Dispatcher
from tests.fixtures.llm.mock_client import ScriptedLLMFactory


def reset_container() -> None:
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


def write_config(
    *,
    tmp_path: Path,
    provider: str,
    db_name: str,
    orchestration_dir: Path | None = None,
    tool_ownership_mode: str = "shared",
    main_agent_tools_allow: list[str] | None = None,
) -> Path:
    config_path = tmp_path / "config.toml"
    sqlite_url = f"sqlite+aiosqlite:///{(tmp_path / db_name).as_posix()}"
    orchestration_block = ""
    if orchestration_dir is not None:
        main_agent_lines = ["\n[orchestration.main_agent]"]
        if main_agent_tools_allow:
            entries = ", ".join([f'"{name}"' for name in main_agent_tools_allow])
            main_agent_lines.append(f"tools_allow = [{entries}]")
        orchestration_block = (
            "\n[orchestration]\n"
            f'directory = "{orchestration_dir.as_posix()}"\n'
            "default_timeout_seconds = 30\n"
            f'tool_ownership_mode = "{tool_ownership_mode}"\n'
        ) + "\n".join(main_agent_lines)
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
        + orchestration_block
        + "\n",
        encoding="utf-8",
    )
    return config_path


def write_agent(
    *,
    agents_dir: Path,
    name: str,
    description: str,
    model_provider: str,
    enabled: bool = True,
    tools_allow: list[str] | None = None,
) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    allow_lines = ""
    if tools_allow:
        allow_lines = "tools_allow:\n" + "".join([f"  - {item}\n" for item in tools_allow])
    enabled_line = "true" if enabled else "false"
    (agents_dir / f"{name}.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"enabled: {enabled_line}\n"
            "mode: agent\n"
            f"model_provider: {model_provider}\n"
            "model: gpt-4o-mini\n"
            f"{allow_lines}"
            "---\n\n"
            f"You are {name}."
        ),
        encoding="utf-8",
    )


async def run_console_turn(
    *,
    config_path: Path,
    llm_factory: ScriptedLLMFactory,
    text: str,
    chat_id: int = 999,
    user_id: int = 777,
):
    reset_container()
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
            return await console_service.wait_for_response(3.0)
        finally:
            await console_service.stop()
            await dispatcher.stop()
            reset_container()
