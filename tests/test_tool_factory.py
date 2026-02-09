from __future__ import annotations

from typing import Any, cast

from minibot.adapters.config.schema import (
    CalculatorToolConfig,
    HTTPClientToolConfig,
    KeyValueMemoryConfig,
    LLMMConfig,
    PlaywrightToolConfig,
    PythonExecToolConfig,
    SchedulerConfig,
    ScheduledPromptsConfig,
    Settings,
    TimeToolConfig,
    ToolsConfig,
)
from minibot.app.event_bus import EventBus
from minibot.core.files import FileReadResponse, StoredFileRecord
from minibot.llm.tools.factory import build_enabled_tools


class _MemoryStub:
    async def append_history(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content

    async def get_history(self, session_id: str, limit: int = 32):
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


class _FileStorageStub:
    async def write_text(self, **kwargs: Any) -> StoredFileRecord:
        del kwargs
        return StoredFileRecord(
            id="file-1",
            relative_path="notes.txt",
            mime_type="text/plain",
            size_bytes=10,
            created_at="2026-01-01T00:00:00Z",
        )

    async def list_files(self, **kwargs: Any) -> list[StoredFileRecord]:
        del kwargs
        return []

    async def read_file(self, **kwargs: Any) -> FileReadResponse:
        del kwargs
        return FileReadResponse(
            path="notes.txt",
            mode="lines",
            offset=0,
            limit=10,
            content="",
            bytes_read=0,
            has_more=False,
        )

    def resolve_absolute_path(self, path: str):
        return path

    def describe_file(self, **kwargs: Any) -> StoredFileRecord:
        del kwargs
        return StoredFileRecord(
            id="file-1",
            relative_path="notes.txt",
            mime_type="text/plain",
            size_bytes=10,
            created_at="2026-01-01T00:00:00Z",
        )


def _settings(
    *,
    kv_enabled: bool,
    http_enabled: bool,
    time_enabled: bool,
    calculator_enabled: bool,
    python_exec_enabled: bool,
    prompts_enabled: bool,
    playwright_enabled: bool,
) -> Settings:
    return Settings(
        llm=LLMMConfig(api_key="secret"),
        tools=ToolsConfig(
            kv_memory=KeyValueMemoryConfig(enabled=kv_enabled),
            http_client=HTTPClientToolConfig(enabled=http_enabled),
            time=TimeToolConfig(enabled=time_enabled),
            calculator=CalculatorToolConfig(enabled=calculator_enabled),
            python_exec=PythonExecToolConfig(enabled=python_exec_enabled),
            playwright=PlaywrightToolConfig(enabled=playwright_enabled),
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
        prompts_enabled=True,
        playwright_enabled=False,
    )

    tools = build_enabled_tools(settings, memory=_MemoryStub(), kv_memory=None, prompt_scheduler=None)
    names = {binding.tool.name for binding in tools}

    assert "chat_history_info" in names
    assert "chat_history_trim" in names
    assert "current_datetime" in names
    assert "calculate_expression" in names
    assert "python_execute" in names
    assert "python_environment_info" in names
    assert "browser_open" not in names
    assert "schedule_prompt" not in names


def test_build_enabled_tools_includes_optional_toolsets() -> None:
    settings = _settings(
        kv_enabled=True,
        http_enabled=True,
        time_enabled=False,
        calculator_enabled=False,
        python_exec_enabled=False,
        prompts_enabled=True,
        playwright_enabled=True,
    )

    tools = build_enabled_tools(
        settings,
        memory=_MemoryStub(),
        kv_memory=cast(Any, _KVStub()),
        prompt_scheduler=cast(Any, _PromptSchedulerStub()),
        event_bus=EventBus(),
        file_storage=cast(Any, _FileStorageStub()),
    )
    names = {binding.tool.name for binding in tools}

    assert {"user_memory_save", "user_memory_get", "user_memory_search"}.issubset(names)
    assert "http_request" in names
    assert {
        "browser_navigate",
        "browser_info",
        "browser_get_data",
        "browser_wait_for",
        "browser_click",
        "browser_query_selector",
        "browser_close",
    }.issubset(names)
    assert {
        "schedule_prompt",
        "cancel_scheduled_prompt",
        "delete_scheduled_prompt",
        "list_scheduled_prompts",
    }.issubset(names)
    assert {"file_write", "file_list", "file_read", "send_file_in_channel"}.issubset(names)
    assert "current_datetime" not in names
    assert "calculate_expression" not in names
    assert "python_execute" not in names
    assert "python_environment_info" not in names
