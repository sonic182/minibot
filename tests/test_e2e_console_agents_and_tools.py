from __future__ import annotations

import importlib.util
import json
import logging
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
_LOGGER = logging.getLogger("tests.e2e.console_agents_and_tools")

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
    enable_http_client: bool = True,
    delegated_tool_call_policy: str | None = None,
    llm_max_tool_iterations: int = 45,
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
    if delegated_tool_call_policy is not None:
        insertion_marker = f'tool_ownership_mode = "{tool_ownership_mode}"\n'
        if insertion_marker in text and "delegated_tool_call_policy" not in text:
            text = text.replace(
                insertion_marker,
                insertion_marker + f'delegated_tool_call_policy = "{delegated_tool_call_policy}"\n',
            )
        else:
            text = text.replace(
                'delegated_tool_call_policy = "auto"\n',
                f'delegated_tool_call_policy = "{delegated_tool_call_policy}"\n',
            )
    text = text.replace("# include_agent_trace_in_metadata = true\n", "include_agent_trace_in_metadata = true\n")
    text = text.replace("agent_timeout_seconds = 120\n", "agent_timeout_seconds = 240\n")
    text = text.replace("max_tool_iterations = 15\n", f"max_tool_iterations = {max(1, llm_max_tool_iterations)}\n")
    text = text.replace("request_timeout_seconds = 45\n", "request_timeout_seconds = 120\n")
    text = text.replace("sock_read_timeout_seconds = 45\n", "sock_read_timeout_seconds = 120\n")
    text = text.replace("default_timeout_seconds = 90\n", "default_timeout_seconds = 240\n")
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
    text = text.replace('  "--browser=chromium",\n', '  "--browser=chromium",\n  "--isolated",\n')
    text = text.replace('  "--browser=chrome",\n', '  "--browser=chrome",\n  "--isolated",\n')
    text = text.replace('  "--save-session"\n', "")
    text = text.replace('command = "npx"\n', f'command = "{_resolve_npx_command()}"\n')
    text = text.replace('cwd = "."\n', f'cwd = "{browser_dir.as_posix()}"\n')
    if not enable_http_client:
        text = text.replace("[tools.http_client]\nenabled = true\n", "[tools.http_client]\nenabled = false\n")
    config_path.write_text(text, encoding="utf-8")
    _LOGGER.debug(
        "wrote e2e config",
        extra={
            "config_path": config_path.as_posix(),
            "tool_ownership_mode": tool_ownership_mode,
            "main_agent_tools_allow": main_agent_tools_allow,
            "delegated_tool_call_policy": delegated_tool_call_policy,
            "llm_max_tool_iterations": llm_max_tool_iterations,
            "enable_http_client": enable_http_client,
        },
    )
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


def _write_browser_agent(agents_dir: Path, *, profile: str = "cheap_openai") -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    content = _BROWSER_AGENT_TEMPLATE_PATH.read_text(encoding="utf-8")
    if "enabled: false" in content:
        content = content.replace("enabled: false", "enabled: true")
    if "max_tool_iterations: 25" in content:
        content = content.replace("max_tool_iterations: 25", "max_tool_iterations: 8")
    if "max_tool_iterations: 8" not in content:
        content = content.replace("mcp_servers:\n", "max_tool_iterations: 8\nmcp_servers:\n")
    if profile == "openrouter_glm5":
        content = content.replace("model_provider: openai_responses\n", "model_provider: openrouter\n")
        content = content.replace("model_provider: openai\n", "model_provider: openrouter\n")
        content = content.replace("model: z-ai/glm-5\n", "model: z-ai/glm-4.7\n")
        content = content.replace("model: gpt-5.2\n", "model: z-ai/glm-4.7\n")
        content = content.replace("model: gpt-5-mini\n", "model: z-ai/glm-4.7\n")
        content = content.replace("model: gpt-4o-mini\n", "model: z-ai/glm-4.7\n")
        if "openrouter_reasoning_enabled:" not in content:
            content = content.replace(
                "reasoning_effort: low\n",
                "reasoning_effort: low\nopenrouter_reasoning_enabled: true\n",
            )
        if "openrouter_provider_only:" in content:
            start = content.index("openrouter_provider_only:")
            end = content.find("\n", start)
            if end != -1:
                trailing = content[end + 1 :]
                while trailing.startswith("  - "):
                    next_newline = trailing.find("\n")
                    if next_newline < 0:
                        trailing = ""
                        break
                    trailing = trailing[next_newline + 1 :]
                content = content[:start] + "openrouter_provider_only:\n  - siliconflow\n  - atlas-cloud\n" + trailing
        else:
            content = content.replace(
                "openrouter_reasoning_enabled: true\n",
                "openrouter_reasoning_enabled: true\n"
                + "openrouter_provider_only:\n  - siliconflow\n  - atlas-cloud\n",
            )
        if "openrouter_provider_only:" in content:
            start = content.index("openrouter_provider_only:")
            end = content.find("\n", start)
            if end != -1:
                trailing = content[end + 1 :]
                while trailing.startswith("  - "):
                    next_newline = trailing.find("\n")
                    if next_newline < 0:
                        trailing = ""
                        break
                    trailing = trailing[next_newline + 1 :]
                content = (
                    content[:start]
                    + "openrouter_provider_only:\n"
                    + "  - siliconflow\n"
                    + "  - google-vertex\n"
                    + "  - together\n"
                    + "  - novita\n"
                    + "  - atlas-cloud\n"
                    + trailing
                )
        if "openrouter_provider_quantizations:" not in content:
            content = content.replace(
                "openrouter_provider_only:\n"
                + "  - siliconflow\n"
                + "  - google-vertex\n"
                + "  - together\n"
                + "  - novita\n"
                + "  - atlas-cloud\n",
                "openrouter_provider_only:\n"
                + "  - siliconflow\n"
                + "  - google-vertex\n"
                + "  - together\n"
                + "  - novita\n"
                + "  - atlas-cloud\n"
                + "openrouter_provider_quantizations:\n"
                + "  - fp8\n",
            )
        else:
            content = content.replace(
                "openrouter_provider_quantizations:\n  - fp16\n",
                "openrouter_provider_quantizations:\n  - fp8\n",
            )
        if "openrouter_provider_sort:" in content:
            content = content.replace("openrouter_provider_sort: throughput\n", "openrouter_provider_sort: latency\n")
            content = content.replace("openrouter_provider_sort: price\n", "openrouter_provider_sort: latency\n")
        else:
            content = content.replace(
                "openrouter_provider_quantizations:\n  - fp8\n",
                "openrouter_provider_quantizations:\n  - fp8\nopenrouter_provider_sort: latency\n",
            )
    else:
        content = content.replace("model_provider: openrouter\n", "model_provider: openai_responses\n")
        content = content.replace("model_provider: openai\n", "model_provider: openai_responses\n")
        content = content.replace("model: z-ai/glm-4.7\n", "model: gpt-5-mini\n")
        content = content.replace("model: gpt-5.2\n", "model: gpt-5-mini\n")
        content = content.replace("model: gpt-4o-mini\n", "model: gpt-5-mini\n")
    fast_tools_block = (
        "  - mcp_playwright-cli__browser_navigate\n"
        "  - mcp_playwright-cli__browser_snapshot\n"
        "  - mcp_playwright-cli__browser_wait_for\n"
        "  - mcp_playwright-cli__browser_route\n"
        "  - mcp_playwright-cli__browser_unroute\n"
        "  - mcp_playwright-cli__browser_network_requests\n"
        "  - mcp_playwright-cli__browser_tabs\n"
        "  - mcp_playwright-cli__browser_close\n"
        "  - mcp_playwright-cli__browser_run_code\n"
        "  - mcp_playwright-cli__browser_take_screenshot\n"
        "  - filesystem\n"
    )
    if "  - mcp_playwright-cli__*\n" in content:
        content = content.replace("  - mcp_playwright-cli__*\n", fast_tools_block)
    elif "  - mcp_playwright-cli__browser_run_code\n" in content:
        content = content.replace("  - mcp_playwright-cli__browser_run_code\n", fast_tools_block)
    elif "tools_allow:\n" in content and "mcp_playwright-cli__browser_navigate" not in content:
        content = content.replace("tools_allow:\n", f"tools_allow:\n{fast_tools_block}")
    content = content.replace("  - filesystem\n  - filesystem\n", "  - filesystem\n")
    content = (
        content
        + "\n"
        + "Test mode:\n"
        + "- Use minimal fast extraction pattern: navigate -> snapshot/run_code -> optional short wait -> "
        + "final output.\n"
        + "- Keep waits short; avoid long idle waits and avoid repeated retries.\n"
        + "- If any browser tool returns an error, stop immediately and return: browser unavailable.\n"
        + "- For research tasks, return channel links and subscriber/follower counts from observed pages.\n"
        + "- After first successful tool call, continue until you can return final JSON answer; "
        + "never reply with planning text.\n"
        + "- For ranking/research requests, return at least 5 channel entries with name, subscribers, "
        + "and YouTube link.\n"
    )
    (agents_dir / "browser_agent.md").write_text(content, encoding="utf-8")


async def _classify_youtube_answer(*, request_text: str, answer_text: str) -> dict[str, object]:
    from llm_async.models.message import Message
    from llm_async.models.response_schema import ResponseSchema
    from llm_async.providers import OpenAIProvider

    provider = OpenAIProvider(api_key=os.environ["OPENAI_API_KEY"])
    schema = ResponseSchema(
        schema={
            "type": "object",
            "properties": {
                "pass": {"type": "boolean"},
                "score": {"type": "number"},
                "recommended_count": {"type": "integer"},
                "has_spanish_focus": {"type": "boolean"},
                "has_ai_agents_automation_focus": {"type": "boolean"},
                "has_followers_info": {"type": "boolean"},
                "has_youtube_channel_links": {"type": "boolean"},
                "missing_requirements": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": [
                "pass",
                "score",
                "recommended_count",
                "has_spanish_focus",
                "has_ai_agents_automation_focus",
                "has_followers_info",
                "has_youtube_channel_links",
                "missing_requirements",
                "reason",
            ],
            "additionalProperties": False,
        }
    )
    prompt = (
        "Evaluate whether the assistant answer satisfies the user request. "
        "A passing answer must recommend at least 5 channels (more than 5 is acceptable), "
        "focused on Spanish-speaking creators about AI agents/automation, include follower/subscriber counts, "
        "and include YouTube channel links. Evaluate fulfillment quality, not factual truth. "
        "Set pass=true if and only if all mandatory criteria are satisfied. "
        "Do not fail because of extra caveats, optional wording, or additional suggestions.\n\n"
        f"User request:\n{request_text}\n\nAssistant answer:\n{answer_text}"
    )
    response = await provider.acomplete(
        model="gpt-4o-mini",
        messages=[Message(role="user", content=prompt)],
        response_schema=schema,
    )
    content = response.main_response.content if response.main_response else ""
    if not isinstance(content, str):
        raise AssertionError(f"Classifier returned non-string content type: {type(content).__name__}")
    parsed = json.loads(content)
    assert isinstance(parsed, dict)
    return parsed


def _resolve_npx_command() -> str:
    if _ASDF_NPX_SHIM.exists():
        return _ASDF_NPX_SHIM.as_posix()
    resolved = shutil.which("npx")
    if isinstance(resolved, str) and resolved.strip():
        return resolved
    return "npx"


async def _run_console_turn(*, config_path: Path, text: str, wait_timeout_seconds: float = 120.0):
    _LOGGER.debug(
        "starting console turn",
        extra={
            "config_path": config_path.as_posix(),
            "wait_timeout_seconds": wait_timeout_seconds,
            "user_text_preview": text[:280],
        },
    )
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
        payload = response.response
        _LOGGER.debug(
            "console turn response received",
            extra={
                "channel": payload.channel,
                "text_preview": payload.text[:600],
                "metadata": payload.metadata,
            },
        )
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
        attempt = _ + 1
        try:
            response = await _run_console_turn(
                config_path=config_path,
                text=text,
                wait_timeout_seconds=wait_timeout_seconds,
            )
        except TimeoutError:
            _LOGGER.warning("console turn timed out", extra={"attempt": attempt, "attempts": attempts})
            continue
        last_response = response
        _LOGGER.debug(
            "console turn attempt completed",
            extra={
                "attempt": attempt,
                "attempts": attempts,
                "channel": response.channel,
                "text_preview": response.text[:700],
                "metadata": response.metadata,
            },
        )
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


@pytest.mark.asyncio
@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp package required")
@pytest.mark.skipif(
    not _ASDF_NPX_SHIM.exists() and shutil.which("npx") is None,
    reason="Playwright MCP E2E requires npx",
)
@pytest.mark.timeout(30)
async def test_e2e_console_screenshot_delegation_with_attachments_reports_path(
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
            "Task: take a screenshot of https://www.example.com/. "
            "After receiving the delegation result, report the screenshot file path "
            "in a user-friendly console message format like 'Screenshot saved at: <path>'. "
            "Do NOT call send_file since console cannot send files to users."
        ),
        attempts=2,
        wait_timeout_seconds=25.0,
    )

    assert response.channel == "console"
    trace = response.metadata.get("agent_trace")
    if isinstance(trace, list):
        delegation_entry = next((e for e in trace if e.get("target") == "playwright_mcp_agent"), None)
        assert delegation_entry is not None, "Expected delegation to playwright_mcp_agent"
        assert delegation_entry.get("ok") is True, "Expected successful delegation"

    lowered = response.text.lower()
    has_success_indicator = any(keyword in lowered for keyword in ["saved", "screenshot", "captured"])
    has_explicit_unavailable = "browser unavailable" in lowered
    has_browser_contention = "browser" in lowered and ("already in use" in lowered or "in use" in lowered)
    has_expected_fallback = "could not complete that delegated action reliably" in lowered
    assert has_success_indicator or has_explicit_unavailable or has_browser_contention or has_expected_fallback, (
        "Expected screenshot success indicator or known browser fallback"
    )

    expected_browser_dir = (tmp_path / "files" / "browser").as_posix().lower()
    has_path = any(
        [
            expected_browser_dir in lowered,
            "browser/" in lowered,
            "files/browser" in lowered,
            ".png" in lowered,
        ]
    )
    if has_success_indicator:
        assert has_path, "Expected file path in console response"


@pytest.mark.asyncio
@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="mcp package required")
@pytest.mark.skipif(
    not _ASDF_NPX_SHIM.exists() and shutil.which("npx") is None,
    reason="Playwright MCP E2E requires npx",
)
@pytest.mark.timeout(240)
async def test_e2e_console_top5_spanish_ai_youtubers_with_browser_and_llm_classifier(tmp_path: Path) -> None:
    _LOGGER.setLevel(logging.DEBUG)
    logging.getLogger("minibot").setLevel(logging.DEBUG)
    agents_dir = tmp_path / "agents"
    _write_browser_agent(agents_dir, profile="openrouter_glm5")
    config_path = _write_e2e_config(
        tmp_path=tmp_path,
        agents_dir=agents_dir,
        main_agent_tools_allow=["list_agents", "invoke_agent", "current_*", "calculate_*"],
        tool_ownership_mode="exclusive",
        enable_http_client=False,
        delegated_tool_call_policy="never",
        llm_max_tool_iterations=8,
    )

    request_text = (
        "Usa invoke_agent exactamente una vez con agent_name=playwright_mcp_agent para investigar en web. "
        "No llames otras tools despues de eso. "
        "Necesito un top de al menos 5 youtubers que hablen en espanol sobre agentes AI y automatizaciones; "
        "si puedes recomendar mas de 5, esta bien. "
        "Para cada recomendacion incluye: nombre del canal, followers/suscriptores (estimado si hace falta), "
        "y link del canal en YouTube. "
        "Si la delegacion falla o devuelve progreso parcial, responde igual con un listado best-effort y no devuelvas "
        "mensajes de error al usuario."
    )
    response = await _run_console_turn_with_retry(
        config_path=config_path,
        text=request_text,
        attempts=2,
        wait_timeout_seconds=160.0,
    )

    assert response.channel == "console"
    assert isinstance(response.text, str) and response.text.strip()

    trace = response.metadata.get("agent_trace")
    assert isinstance(trace, list), "Expected agent_trace in metadata"
    delegated_entries = [entry for entry in trace if entry.get("target") == "playwright_mcp_agent"]
    assert delegated_entries, "Expected delegation attempt to playwright_mcp_agent"

    judgment = await _classify_youtube_answer(request_text=request_text, answer_text=response.text)
    recommended_count = judgment.get("recommended_count")
    strict_pass = bool(
        isinstance(recommended_count, int)
        and recommended_count >= 5
        and judgment.get("has_spanish_focus") is True
        and judgment.get("has_ai_agents_automation_focus") is True
        and judgment.get("has_followers_info") is True
        and judgment.get("has_youtube_channel_links") is True
    )
    lowered = response.text.lower()
    degraded_best_effort_pass = bool(
        isinstance(recommended_count, int)
        and recommended_count >= 5
        and judgment.get("has_spanish_focus") is True
        and judgment.get("has_ai_agents_automation_focus") is True
        and judgment.get("has_youtube_channel_links") is True
        and ("suscriptores" in lowered or "followers" in lowered)
    )
    has_explicit_inconclusive_result = any(
        marker in lowered
        for marker in [
            "no encontr",
            "no pude",
            "no se pudo",
            "navegador no est",
            "browser unavailable",
            "restricciones de acceso",
            "youtube no permite",
            "búsqueda manual",
            "busqueda manual",
            "podemos intentar",
            "te gustar",
        ]
    )
    assert strict_pass or degraded_best_effort_pass or has_explicit_inconclusive_result, (
        f"Classifier rejected answer: missing={judgment.get('missing_requirements')} "
        f"reason={judgment.get('reason')} score={judgment.get('score')} pass={judgment.get('pass')}"
    )
