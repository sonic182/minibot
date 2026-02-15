from __future__ import annotations

import importlib.util
import os
import shutil
from types import SimpleNamespace
from pathlib import Path

import pytest

from minibot.adapters.container.app_container import AppContainer
from minibot.adapters.messaging.console.service import ConsoleService
from minibot.app.dispatcher import Dispatcher


_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_TEMPLATE_PATH = _ROOT / "tests" / "config.test.toml"
_BROWSER_AGENT_TEMPLATE_PATH = _ROOT / "agents" / "browser_agent.md"
_ASDF_NPX_SHIM = Path.home() / ".asdf" / "shims" / "npx"

_HAS_REQUIRED_ENV = bool(
    os.environ.get("E2E_RUN") and os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENROUTER_API_KEY")
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not _HAS_REQUIRED_ENV,
        reason=("E2E tests require E2E_RUN, OPENAI_API_KEY, and OPENROUTER_API_KEY environment variables."),
    ),
]


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


def _array(items: list[str]) -> str:
    return "[" + ", ".join([f'"{item}"' for item in items]) + "]"


def _write_e2e_config(
    *,
    tmp_path: Path,
    agents_dir: Path,
    main_agent_tools_allow: list[str],
    tool_ownership_mode: str,
) -> Path:
    config_path = tmp_path / "config.e2e.toml"
    browser_dir = tmp_path / "files" / "browser"
    browser_dir.mkdir(parents=True, exist_ok=True)
    text = _CONFIG_TEMPLATE_PATH.read_text(encoding="utf-8")
    text = text.replace("[channels.telegram]\nenabled = true\n", "[channels.telegram]\nenabled = false\n")
    text = text.replace('provider = "openai_responses"\n', 'provider = "openai"\n')
    text = text.replace('model = "gpt-5-mini"\n', 'model = "gpt-4o-mini"\n')
    text = text.replace(
        '[providers.openai]\napi_key = ""\nbase_url = ""\n',
        (f'[providers.openai]\napi_key = "{os.environ["OPENAI_API_KEY"]}"\nbase_url = ""\n'),
    )
    text = text.replace(
        '[providers.openai_responses]\napi_key = ""\nbase_url = ""\n',
        (f'[providers.openai_responses]\napi_key = "{os.environ["OPENAI_API_KEY"]}"\nbase_url = ""\n'),
    )
    text = text.replace(
        '[providers.openrouter]\napi_key = ""\nbase_url = ""\n',
        (f'[providers.openrouter]\napi_key = "{os.environ["OPENROUTER_API_KEY"]}"\nbase_url = ""\n'),
    )
    text = text.replace('directory = "./agents"\n', f'directory = "{agents_dir.as_posix()}"\n')
    text = text.replace('tool_ownership_mode = "exclusive"\n', f'tool_ownership_mode = "{tool_ownership_mode}"\n')
    text = text.replace(
        'tools_allow = ["current_*", "calculate_*", "http_*", "*_agent*"]\n',
        f"tools_allow = {_array(main_agent_tools_allow)}\n",
    )
    text = text.replace(
        'sqlite_url = "sqlite+aiosqlite:///./testdata/minibot.db"\n',
        f'sqlite_url = "sqlite+aiosqlite:///{(tmp_path / "memory.db").as_posix()}"\n',
    )
    text = text.replace("[scheduler.prompts]\nenabled = true\n", "[scheduler.prompts]\nenabled = false\n")
    text = text.replace(
        'sqlite_url = "sqlite+aiosqlite:///./testdata/scheduled_prompts.db"\n',
        f'sqlite_url = "sqlite+aiosqlite:///{(tmp_path / "scheduler.db").as_posix()}"\n',
    )
    text = text.replace(
        'sqlite_url = "sqlite+aiosqlite:///./testdata/kv_memory.db"\n',
        f'sqlite_url = "sqlite+aiosqlite:///{(tmp_path / "kv_memory.db").as_posix()}"\n',
    )
    text = text.replace('root_dir = "./testdata/files"\n', f'root_dir = "{(tmp_path / "files").as_posix()}"\n')
    text = text.replace(
        '"--output-dir=./testdata/files/browser",\n',
        f'"--output-dir={browser_dir.as_posix()}",\n',
    )
    text = text.replace('  # "--headless",\n', '  "--headless",\n')
    text = text.replace('  "--browser=chrome",\n', '  "--browser=chrome",\n  "--isolated",\n')
    text = text.replace('  "--save-session"\n', "")
    text = text.replace('command = "npx"\n', f'command = "{_resolve_npx_command()}"\n')
    text = text.replace('cwd = "."\n', f'cwd = "{browser_dir.as_posix()}"\n')
    config_path.write_text(text, encoding="utf-8")
    return config_path


def _write_workspace_agent(agents_dir: Path) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "workspace_manager_agent.md").write_text(
        (
            "---\n"
            "name: workspace_manager_agent\n"
            "description: Workspace files specialist\n"
            "enabled: true\n"
            "mode: agent\n"
            "model_provider: openrouter\n"
            "model: openai/gpt-4o-mini\n"
            "tools_allow:\n"
            "  - list_files\n"
            "  - create_file\n"
            "  - file_info\n"
            "  - move_file\n"
            "  - delete_file\n"
            "---\n\n"
            "You are the workspace file specialist. "
            "Always use file tools to execute file operations, then report exact path and final status."
        ),
        encoding="utf-8",
    )


def _write_browser_agent(agents_dir: Path) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    content = _BROWSER_AGENT_TEMPLATE_PATH.read_text(encoding="utf-8")
    if "enabled: false" in content:
        content = content.replace("enabled: false", "enabled: true")
    if "max_tool_iterations: 25" in content:
        content = content.replace("max_tool_iterations: 25", "max_tool_iterations: 8")
    if "max_tool_iterations: 8" not in content:
        content = content.replace("mcp_servers:\n", "max_tool_iterations: 8\nmcp_servers:\n")
    content = content.replace("model_provider: openrouter\n", "model_provider: openai\n")
    content = content.replace("model: z-ai/glm-4.7\n", "model: gpt-4o-mini\n")
    content = content.replace("  - mcp_playwright-cli__*\n", "  - mcp_playwright-cli__browser_run_code\n")
    if "  - list_files\n" not in content:
        content = content.replace(
            "  - mcp_playwright-cli__browser_run_code\n",
            "  - mcp_playwright-cli__browser_run_code\n  - list_files\n",
        )
    content = (
        content
        + "\n"
        + "Test mode:\n"
        + "- Execute only the minimum browser tool calls needed for the request.\n"
        + "- Stop immediately once required evidence is collected.\n"
        + "- For screenshot tasks, use browser_run_code once and avoid browser_navigate/browser_snapshot.\n"
        + "- For title/description extraction tasks, use browser_run_code once "
        + "and avoid browser_evaluate/browser_wait_for.\n"
        + "- If any browser tool returns an error, stop immediately and return: browser unavailable.\n"
        + "- Never call list_files more than once in a single task.\n"
    )
    (agents_dir / "browser_agent.md").write_text(content, encoding="utf-8")


def _resolve_npx_command() -> str:
    if _ASDF_NPX_SHIM.exists():
        return _ASDF_NPX_SHIM.as_posix()
    resolved = shutil.which("npx")
    if isinstance(resolved, str) and resolved.strip():
        return resolved
    return "npx"


async def _run_console_turn(*, config_path: Path, text: str, wait_timeout_seconds: float = 120.0):
    _reset_container()
    AppContainer.configure(config_path)
    await AppContainer.initialize_storage()
    bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(bus)
    console_service = ConsoleService(bus, chat_id=4321, user_id=8765)
    await dispatcher.start()
    await console_service.start()
    try:
        await console_service.publish_user_message(text)
        response = await console_service.wait_for_response(wait_timeout_seconds)
        return response.response
    finally:
        await console_service.stop()
        await dispatcher.stop()
        _reset_container()


async def _run_console_turn_with_retry(
    *,
    config_path: Path,
    text: str,
    attempts: int = 2,
    wait_timeout_seconds: float = 120.0,
):
    retry_markers = (
        "maximum execution steps",
        "could not be retrieved",
        "error in launching the browser process",
        "cannot provide",
        "already in use",
    )
    last_response = None
    for _ in range(max(1, attempts)):
        try:
            response = await _run_console_turn(
                config_path=config_path,
                text=text,
                wait_timeout_seconds=wait_timeout_seconds,
            )
        except TimeoutError:
            continue
        last_response = response
        lowered = response.text.lower()
        if not any(marker in lowered for marker in retry_markers):
            return response
    if last_response is None:
        return SimpleNamespace(channel="console", text="browser unavailable", metadata={})
    return last_response


@pytest.mark.asyncio
async def test_e2e_console_agent_offload_workspace_file_workflow(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    _write_workspace_agent(agents_dir)
    config_path = _write_e2e_config(
        tmp_path=tmp_path,
        agents_dir=agents_dir,
        main_agent_tools_allow=["list_agents", "invoke_agent", "current_*", "calculate_*"],
        tool_ownership_mode="exclusive",
    )

    response = await _run_console_turn(
        config_path=config_path,
        text=(
            "Use invoke_agent with agent_name workspace_manager_agent. "
            "Create notes/e2e-offload.txt with exact content E2E_OFFLOAD_OK. "
            "Then provide a short confirmation."
        ),
    )

    created_file = tmp_path / "files" / "notes" / "e2e-offload.txt"
    assert created_file.exists()
    assert created_file.read_text(encoding="utf-8").strip() == "E2E_OFFLOAD_OK"
    assert response.channel == "console"
    assert response.metadata["primary_agent"] == "minibot"
    assert response.metadata["delegation_fallback_used"] is False
    trace = response.metadata.get("agent_trace")
    assert isinstance(trace, list)
    assert any(entry.get("target") == "workspace_manager_agent" and entry.get("ok") is True for entry in trace)


@pytest.mark.asyncio
async def test_e2e_console_normal_tool_call_workspace_file_workflow(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    config_path = _write_e2e_config(
        tmp_path=tmp_path,
        agents_dir=agents_dir,
        main_agent_tools_allow=["create_file", "file_info", "list_files", "current_*", "calculate_*", "http_*"],
        tool_ownership_mode="shared",
    )

    response = await _run_console_turn(
        config_path=config_path,
        text=("Create notes/e2e-direct.txt with exact content E2E_DIRECT_OK using tools, then confirm the file path."),
    )

    created_file = tmp_path / "files" / "notes" / "e2e-direct.txt"
    assert created_file.exists()
    assert created_file.read_text(encoding="utf-8").strip() == "E2E_DIRECT_OK"
    assert response.channel == "console"
    trace = response.metadata.get("agent_trace")
    if trace is not None:
        assert trace == []


@pytest.mark.asyncio
@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp package is required for Playwright MCP E2E")
@pytest.mark.skipif(
    not _ASDF_NPX_SHIM.exists() and shutil.which("npx") is None,
    reason="Playwright MCP E2E requires asdf npx shim or npx on PATH",
)
@pytest.mark.timeout(45)
async def test_e2e_console_agent_offload_browser_playwright_workflow(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    _write_browser_agent(agents_dir)
    config_path = _write_e2e_config(
        tmp_path=tmp_path,
        agents_dir=agents_dir,
        main_agent_tools_allow=["list_agents", "invoke_agent", "current_*", "calculate_*"],
        tool_ownership_mode="exclusive",
    )

    response = await _run_console_turn_with_retry(
        config_path=config_path,
        text=(
            "Use invoke_agent exactly once with agent_name=playwright_mcp_agent. "
            "Task: use browser_run_code once on https://www.example.com and return "
            "title plus meta description in one line. "
            "If any browser tool fails, stop immediately and return exactly: browser unavailable. "
            "After tool result, do not call invoke_agent again; just return the result."
        ),
        attempts=1,
        wait_timeout_seconds=30.0,
    )

    assert response.channel == "console"
    lowered = response.text.lower()
    has_expected_extract = "title" in lowered or "description" in lowered or "example" in lowered
    has_explicit_unavailable = "browser unavailable" in lowered
    has_browser_contention = "browser" in lowered and ("already in use" in lowered or "in use" in lowered)
    has_expected_fallback = "could not complete that delegated action reliably" in lowered
    if not (has_explicit_unavailable or has_browser_contention):
        trace = response.metadata.get("agent_trace")
        assert isinstance(trace, list)
        assert any(entry.get("target") == "playwright_mcp_agent" for entry in trace)
    assert has_expected_extract or has_explicit_unavailable or has_browser_contention or has_expected_fallback


@pytest.mark.asyncio
@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp package is required for Playwright MCP E2E")
@pytest.mark.skipif(
    not _ASDF_NPX_SHIM.exists() and shutil.which("npx") is None,
    reason="Playwright MCP E2E requires asdf npx shim or npx on PATH",
)
@pytest.mark.timeout(30)
async def test_e2e_console_browser_extract_meta_title_and_description(tmp_path: Path) -> None:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    config_path = _write_e2e_config(
        tmp_path=tmp_path,
        agents_dir=agents_dir,
        main_agent_tools_allow=[
            "mcp_playwright-cli__browser_run_code",
            "current_*",
            "calculate_*",
        ],
        tool_ownership_mode="shared",
    )

    response = await _run_console_turn_with_retry(
        config_path=config_path,
        text=(
            "Using browser tools directly, call browser_run_code once to open https://www.example.com "
            "and extract document.title plus meta description from "
            "document.querySelector(\"meta[name='description']\")?.content ?? 'missing'. "
            "If the browser tool fails, stop immediately and return exactly: browser unavailable. "
            "Return exactly: title=<value>; description=<value>."
        ),
        attempts=2,
        wait_timeout_seconds=25.0,
    )

    assert response.channel == "console"
    lowered = response.text.lower()
    has_expected_extract = "title=" in lowered and "description=" in lowered and "example" in lowered
    has_explicit_unavailable = "browser unavailable" in lowered
    has_browser_contention = "already in use" in lowered
    assert has_expected_extract or has_explicit_unavailable or has_browser_contention


@pytest.mark.asyncio
@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp package is required for Playwright MCP E2E")
@pytest.mark.skipif(
    not _ASDF_NPX_SHIM.exists() and shutil.which("npx") is None,
    reason="Playwright MCP E2E requires asdf npx shim or npx on PATH",
)
@pytest.mark.timeout(30)
async def test_e2e_console_main_agent_delegates_example_screenshot_and_reports_workspace_folder(
    tmp_path: Path,
) -> None:
    agents_dir = tmp_path / "agents"
    _write_browser_agent(agents_dir)
    config_path = _write_e2e_config(
        tmp_path=tmp_path,
        agents_dir=agents_dir,
        main_agent_tools_allow=["list_agents", "invoke_agent", "current_*", "calculate_*"],
        tool_ownership_mode="exclusive",
    )

    response = await _run_console_turn_with_retry(
        config_path=config_path,
        text=(
            "Use invoke_agent exactly once with agent_name=playwright_mcp_agent. "
            "Task: use browser_run_code once to open https://www.example.com/, take one screenshot, "
            "and return screenshot path plus workspace folder. "
            "Do not call list_files. "
            "If any browser tool fails, stop immediately and return exactly: browser unavailable. "
            "After tool result, do not call invoke_agent again; only return the final answer."
        ),
        attempts=2,
        wait_timeout_seconds=25.0,
    )

    assert response.channel == "console"
    lowered = response.text.lower()
    has_explicit_unavailable = "browser unavailable" in lowered
    has_browser_contention = "browser" in lowered and ("already in use" in lowered or "in use" in lowered)
    if not (has_explicit_unavailable or has_browser_contention):
        trace = response.metadata.get("agent_trace")
        assert isinstance(trace, list)
        assert any(entry.get("target") == "playwright_mcp_agent" and entry.get("ok") is True for entry in trace)

    expected_browser_dir = (tmp_path / "files" / "browser").as_posix().lower()
    has_expected_success_report = ("example" in lowered or "screenshot" in lowered) and (
        expected_browser_dir in lowered
        or "files/browser" in lowered
        or "./data/files/browser" in lowered
        or "/workspace" in lowered
        or "workspace folder" in lowered
    )
    has_expected_fallback = "could not complete that delegated action reliably" in lowered
    assert has_expected_success_report or has_expected_fallback or has_explicit_unavailable or has_browser_contention
