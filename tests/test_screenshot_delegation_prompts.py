"""
Tests to ensure screenshot delegation prompts contain critical anti-base64 instructions.
This prevents expensive token waste from returning base64-encoded images.
"""

from pathlib import Path

from minibot.shared.prompt_loader import load_channel_prompt

_ROOT = Path(__file__).resolve().parent.parent


def test_telegram_channel_prompt_contains_attachment_handling():
    """Verify Telegram channel prompt has attachment handling instructions."""
    prompts_dir = str(_ROOT / "prompts")
    telegram_prompt = load_channel_prompt(prompts_dir, "telegram")

    assert telegram_prompt is not None, "Telegram channel prompt not found"

    critical_phrases = [
        "attachments",
        'filesystem(action="send")',
        "NEVER return base64 data",
    ]

    for phrase in critical_phrases:
        assert phrase in telegram_prompt, f"Missing critical phrase in Telegram prompt: '{phrase}'"


def test_console_channel_prompt_contains_attachment_handling():
    """Verify console channel prompt has attachment handling instructions."""
    prompts_dir = str(_ROOT / "prompts")
    console_prompt = load_channel_prompt(prompts_dir, "console")

    assert console_prompt is not None, "Console channel prompt not found"

    critical_phrases = [
        "console cannot send files",
        "report file paths in your text response",
        'Do NOT call filesystem(action="send")',
        "attachments",
    ]

    for phrase in critical_phrases:
        assert phrase in console_prompt, f"Missing critical phrase in console prompt: '{phrase}'"


def test_prompt_loader_caches_prompts():
    """Verify prompt loader caching works correctly."""
    prompts_dir = str(_ROOT / "prompts")

    # Load twice - should hit cache
    first_load = load_channel_prompt(prompts_dir, "telegram")
    second_load = load_channel_prompt(prompts_dir, "telegram")

    assert first_load == second_load, "Cached prompts should be identical"
    assert first_load is second_load, "Should return same cached object"


def test_browser_agent_tool_access():
    """Verify browser agent has access to required tools."""
    agents_dir = _ROOT / "agents"
    agent_file = agents_dir / "browser_agent.md"

    content = agent_file.read_text(encoding="utf-8")

    # Should have playwright MCP tools configured
    assert "mcp_servers:" in content
    assert "playwright-cli" in content

    # Should have filesystem for path confirmation
    assert "tools_allow:" in content
    assert "filesystem" in content
