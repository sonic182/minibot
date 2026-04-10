from __future__ import annotations

from typing import Any, cast

import pytest

from minibot.adapters.config.schema import (
    ApplyPatchToolConfig,
    AudioTranscriptionToolConfig,
    BashToolConfig,
    BrowserToolConfig,
    CalculatorToolConfig,
    FileStorageToolConfig,
    GrepToolConfig,
    HTTPClientToolConfig,
    KeyValueMemoryConfig,
    LLMMConfig,
    MCPServerConfig,
    MCPToolConfig,
    PythonExecToolConfig,
    ScheduledPromptsConfig,
    SchedulerConfig,
    Settings,
    TimeToolConfig,
    ToolsConfig,
)
from minibot.llm.tools.factory import build_enabled_tools


class _MemoryStub:
    async def append_history(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content

    async def get_history(self, session_id: str, limit: int | None = None):
        del session_id, limit
        return []

    async def count_history(self, session_id: str) -> int:
        del session_id
        return 0

    async def trim_history(self, session_id: str, keep_latest: int) -> int:
        del session_id, keep_latest
        return 0


class _KVStub:
    async def save_entry(self, **kwargs: Any):
        del kwargs
        return None

    async def get_entry(self, **kwargs: Any):
        del kwargs
        return None

    async def search_entries(self, **kwargs: Any):
        del kwargs
        return None

    async def list_entries(self, **kwargs: Any):
        del kwargs
        return None

    async def delete_entry(self, **kwargs: Any):
        del kwargs
        return False


class _PromptSchedulerStub:
    async def schedule_prompt(self, **kwargs: Any):
        del kwargs
        return None

    async def cancel_prompt(self, **kwargs: Any):
        del kwargs
        return None

    async def list_prompts(self, **kwargs: Any):
        del kwargs
        return []

    async def delete_prompt(self, **kwargs: Any):
        del kwargs
        return {"job": None, "deleted": False, "stopped_before_delete": False, "reason": "not_found"}


class _EventBusStub:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def _settings(
    *,
    kv_enabled: bool,
    http_enabled: bool,
    time_enabled: bool,
    calculator_enabled: bool,
    python_exec_enabled: bool,
    bash_enabled: bool,
    apply_patch_enabled: bool,
    prompts_enabled: bool,
    file_storage_enabled: bool,
    audio_transcription_enabled: bool,
    grep_enabled: bool,
) -> Settings:
    return Settings(
        llm=LLMMConfig(api_key="secret"),
        tools=ToolsConfig(
            kv_memory=KeyValueMemoryConfig(enabled=kv_enabled),
            http_client=HTTPClientToolConfig(enabled=http_enabled),
            time=TimeToolConfig(enabled=time_enabled),
            calculator=CalculatorToolConfig(enabled=calculator_enabled),
            python_exec=PythonExecToolConfig(enabled=python_exec_enabled),
            bash=BashToolConfig(enabled=bash_enabled),
            apply_patch=ApplyPatchToolConfig(enabled=apply_patch_enabled),
            file_storage=FileStorageToolConfig(enabled=file_storage_enabled),
            grep=GrepToolConfig(enabled=grep_enabled),
            browser=BrowserToolConfig(output_dir="./data/files/browser"),
            audio_transcription=AudioTranscriptionToolConfig(enabled=audio_transcription_enabled),
        ),
        scheduler=SchedulerConfig(prompts=ScheduledPromptsConfig(enabled=prompts_enabled)),
    )


def test_build_enabled_tools_defaults_to_chat_memory_and_time() -> None:
    settings = _settings(
        kv_enabled=False,
        http_enabled=False,
        time_enabled=True,
        calculator_enabled=True,
        python_exec_enabled=True,
        bash_enabled=False,
        apply_patch_enabled=False,
        prompts_enabled=True,
        file_storage_enabled=False,
        audio_transcription_enabled=False,
        grep_enabled=False,
    )

    tools = build_enabled_tools(settings, memory=_MemoryStub(), kv_memory=None, prompt_scheduler=None, event_bus=None)
    names = {binding.tool.name for binding in tools}

    assert "chat_history_info" in names
    assert "chat_history_trim" in names
    assert "current_datetime" in names
    assert "datetime_now" not in names
    assert "calculate_expression" in names
    assert "calculator" not in names
    assert "python_execute" in names
    assert "python_environment_info" in names
    assert "bash" not in names
    assert "apply_patch" not in names
    assert "schedule_prompt" not in names


def test_build_enabled_tools_includes_optional_toolsets() -> None:
    settings = _settings(
        kv_enabled=True,
        http_enabled=True,
        time_enabled=False,
        calculator_enabled=False,
        python_exec_enabled=False,
        bash_enabled=False,
        apply_patch_enabled=False,
        prompts_enabled=True,
        file_storage_enabled=True,
        audio_transcription_enabled=False,
        grep_enabled=False,
    )

    event_bus = _EventBusStub()
    tools = build_enabled_tools(
        settings,
        memory=_MemoryStub(),
        kv_memory=cast(Any, _KVStub()),
        prompt_scheduler=cast(Any, _PromptSchedulerStub()),
        event_bus=cast(Any, event_bus),
    )
    names = {binding.tool.name for binding in tools}

    assert "memory" in names
    assert "user_memory_save" not in names
    assert "user_memory_get" not in names
    assert "user_memory_search" not in names
    assert "user_memory_delete" not in names
    assert "http_request" in names
    assert "http_client" not in names
    assert {
        "schedule_prompt",
        "cancel_scheduled_prompt",
        "delete_scheduled_prompt",
        "list_scheduled_prompts",
    }.issubset(names)
    assert {
        "filesystem",
        "glob_files",
        "read_file",
        "self_insert_artifact",
    }.issubset(names)
    assert "list_files" not in names
    assert "create_file" not in names
    assert "file_info" not in names
    assert "move_file" not in names
    assert "delete_file" not in names
    assert "send_file" not in names
    assert "artifact_insert" not in names
    assert "current_datetime" not in names
    assert "calculate_expression" not in names
    assert "python_execute" not in names
    assert "python_environment_info" not in names
    assert "bash" not in names
    assert "apply_patch" not in names
    assert "transcribe_audio" not in names


def test_build_enabled_tools_includes_audio_transcription_when_enabled(monkeypatch) -> None:
    settings = _settings(
        kv_enabled=False,
        http_enabled=False,
        time_enabled=False,
        calculator_enabled=False,
        python_exec_enabled=False,
        bash_enabled=False,
        apply_patch_enabled=False,
        prompts_enabled=False,
        file_storage_enabled=True,
        audio_transcription_enabled=True,
        grep_enabled=False,
    )

    class _FakeAudioTool:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        def bindings(self) -> list[Any]:
            return [type("Binding", (), {"tool": type("Tool", (), {"name": "transcribe_audio"})()})()]

    monkeypatch.setattr("minibot.llm.tools.factory.AudioTranscriptionTool", _FakeAudioTool)
    tools = build_enabled_tools(settings, memory=_MemoryStub(), kv_memory=None, prompt_scheduler=None, event_bus=None)
    names = {binding.tool.name for binding in tools}

    assert "transcribe_audio" in names


def test_build_enabled_tools_rejects_audio_transcription_without_file_storage() -> None:
    settings = _settings(
        kv_enabled=False,
        http_enabled=False,
        time_enabled=False,
        calculator_enabled=False,
        python_exec_enabled=False,
        bash_enabled=False,
        apply_patch_enabled=False,
        prompts_enabled=False,
        file_storage_enabled=False,
        audio_transcription_enabled=True,
        grep_enabled=False,
    )

    with pytest.raises(ValueError, match="tools.audio_transcription.enabled requires tools.file_storage.enabled"):
        _ = build_enabled_tools(settings, memory=_MemoryStub(), kv_memory=None, prompt_scheduler=None, event_bus=None)


def test_build_enabled_tools_overrides_playwright_output_dir(monkeypatch) -> None:
    captured_args: list[str] = []

    class _FakeMCPClient:
        def __init__(self, **kwargs: Any) -> None:
            captured_args.extend(list(kwargs.get("args") or []))

    class _FakeMCPBridge:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        def build_bindings(self) -> list[Any]:
            return []

    monkeypatch.setattr("minibot.llm.tools.factory.MCPClient", _FakeMCPClient)
    monkeypatch.setattr("minibot.llm.tools.factory.MCPToolBridge", _FakeMCPBridge)

    settings = Settings(
        llm=LLMMConfig(api_key="secret"),
        tools=ToolsConfig(
            kv_memory=KeyValueMemoryConfig(enabled=False),
            http_client=HTTPClientToolConfig(enabled=False),
            time=TimeToolConfig(enabled=False),
            calculator=CalculatorToolConfig(enabled=False),
            python_exec=PythonExecToolConfig(enabled=False),
            bash=BashToolConfig(enabled=False),
            apply_patch=ApplyPatchToolConfig(enabled=False),
            file_storage=FileStorageToolConfig(enabled=False),
            grep=GrepToolConfig(enabled=False),
            browser=BrowserToolConfig(output_dir="./custom/browser-out"),
            mcp=MCPToolConfig(
                enabled=True,
                servers=[
                    MCPServerConfig(
                        name="playwright-cli",
                        transport="stdio",
                        command="npx",
                        args=["@playwright/mcp@0.0.64", "--output-dir=./data/files/browser", "--save-session"],
                    )
                ],
            ),
        ),
        scheduler=SchedulerConfig(prompts=ScheduledPromptsConfig(enabled=False)),
    )

    _ = build_enabled_tools(settings, memory=_MemoryStub(), kv_memory=None, prompt_scheduler=None, event_bus=None)

    assert "--output-dir=./custom/browser-out" in captured_args
    assert "--output-dir=./data/files/browser" not in captured_args


def test_build_enabled_tools_includes_bash_when_enabled() -> None:
    settings = _settings(
        kv_enabled=False,
        http_enabled=False,
        time_enabled=False,
        calculator_enabled=False,
        python_exec_enabled=False,
        bash_enabled=True,
        apply_patch_enabled=False,
        prompts_enabled=False,
        file_storage_enabled=False,
        audio_transcription_enabled=False,
        grep_enabled=False,
    )

    tools = build_enabled_tools(settings, memory=_MemoryStub(), kv_memory=None, prompt_scheduler=None, event_bus=None)
    names = {binding.tool.name for binding in tools}
    assert "bash" in names


def test_build_enabled_tools_includes_apply_patch_when_enabled() -> None:
    settings = _settings(
        kv_enabled=False,
        http_enabled=False,
        time_enabled=False,
        calculator_enabled=False,
        python_exec_enabled=False,
        bash_enabled=False,
        apply_patch_enabled=True,
        prompts_enabled=False,
        file_storage_enabled=False,
        audio_transcription_enabled=False,
        grep_enabled=False,
    )

    tools = build_enabled_tools(settings, memory=_MemoryStub(), kv_memory=None, prompt_scheduler=None, event_bus=None)
    names = {binding.tool.name for binding in tools}
    assert "apply_patch" in names


def test_build_enabled_tools_includes_grep_when_enabled() -> None:
    settings = _settings(
        kv_enabled=False,
        http_enabled=False,
        time_enabled=False,
        calculator_enabled=False,
        python_exec_enabled=False,
        bash_enabled=False,
        apply_patch_enabled=False,
        prompts_enabled=False,
        file_storage_enabled=True,
        audio_transcription_enabled=False,
        grep_enabled=True,
    )

    tools = build_enabled_tools(settings, memory=_MemoryStub(), kv_memory=None, prompt_scheduler=None, event_bus=None)
    names = {binding.tool.name for binding in tools}
    assert "grep" in names


def test_build_enabled_tools_rejects_grep_without_file_storage() -> None:
    settings = _settings(
        kv_enabled=False,
        http_enabled=False,
        time_enabled=False,
        calculator_enabled=False,
        python_exec_enabled=False,
        bash_enabled=False,
        apply_patch_enabled=False,
        prompts_enabled=False,
        file_storage_enabled=False,
        audio_transcription_enabled=False,
        grep_enabled=True,
    )

    with pytest.raises(ValueError, match="tools.grep.enabled requires tools.file_storage.enabled"):
        _ = build_enabled_tools(settings, memory=_MemoryStub(), kv_memory=None, prompt_scheduler=None, event_bus=None)
