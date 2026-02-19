from __future__ import annotations

import pytest

from minibot.llm.tools.description_loader import load_tool_description


def test_load_known_description_returns_text() -> None:
    text = load_tool_description("memory")
    assert isinstance(text, str)
    assert len(text) > 0


def test_load_description_is_stripped() -> None:
    text = load_tool_description("filesystem")
    assert text == text.strip()


def test_load_description_missing_raises_file_not_found() -> None:
    with pytest.raises(FileNotFoundError, match="Missing tool description file"):
        load_tool_description("__nonexistent_tool__")


def test_load_description_is_cached() -> None:
    first = load_tool_description("glob_files")
    second = load_tool_description("glob_files")
    assert first is second


def test_all_builtin_descriptions_load() -> None:
    names = [
        "chat_history_info",
        "chat_history_trim",
        "filesystem",
        "glob_files",
        "read_file",
        "self_insert_artifact",
        "memory",
        "list_agents",
        "invoke_agent",
    ]
    for name in names:
        text = load_tool_description(name)
        assert isinstance(text, str) and len(text) > 0, f"empty or missing description for {name!r}"
