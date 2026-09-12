from __future__ import annotations

from minibot.shared.tool_call_display import ToolCallDisplay, _is_secret, summarize_tool_call


def test_exact_secret_keys_are_masked() -> None:
    for key in ("token", "password", "secret", "api_key", "authorization", "cookie", "env", "headers"):
        assert _is_secret(key), key


def test_secret_key_variants_are_masked() -> None:
    for key in ("request_headers", "session_cookie", "env_vars", "auth_token", "client_secret", "api_key_value"):
        assert _is_secret(key), key


def test_benign_keys_are_not_masked() -> None:
    for key in ("path", "command", "url", "pattern", "action"):
        assert not _is_secret(key), key


def test_summarize_tool_call_masks_secret_in_generic_fallback() -> None:
    call = ToolCallDisplay(
        phase="started",
        tool_name="unknown_mcp_tool",
        arguments={"request_headers": "Bearer sekrit", "path": "/tmp/foo"},
    )
    line = summarize_tool_call(call)
    assert "sekrit" not in line
    assert "<redacted>" in line
    assert "/tmp/foo" in line


def test_summarize_tool_call_redacts_url_query_secrets() -> None:
    call = ToolCallDisplay(
        phase="started",
        tool_name="http_request",
        arguments={"method": "GET", "url": "https://example.com/x?api_key=sekrit&q=1"},
    )
    line = summarize_tool_call(call)
    assert "sekrit" not in line
    assert "example.com/x" in line
    assert "q=1" in line


def test_summarize_tool_call_clips_long_values() -> None:
    call = ToolCallDisplay(phase="started", tool_name="bash", arguments={"command": "x" * 500})
    line = summarize_tool_call(call)
    assert len(line) <= 160
    assert line.endswith("…")


def test_summarize_tool_call_includes_failed_error() -> None:
    call = ToolCallDisplay(phase="failed", tool_name="read_file", arguments={"path": "/tmp/x"}, error="boom")
    line = summarize_tool_call(call)
    assert "failed" in line
    assert "boom" in line


def test_generic_detail_caps_fallback_args() -> None:
    call = ToolCallDisplay(
        phase="started",
        tool_name="unknown_mcp_tool",
        arguments={"a": "1", "b": "2", "c": "3", "d": "4"},
    )
    line = summarize_tool_call(call)
    assert sum(line.count(f"{key}=") for key in "abcd") <= 3
