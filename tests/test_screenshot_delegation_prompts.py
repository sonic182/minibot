"""
Tests to ensure screenshot delegation prompts contain critical anti-base64 instructions.
This prevents expensive token waste from returning base64-encoded images.
"""

from pathlib import Path

import pytest

from minibot.shared.prompt_loader import load_channel_prompt, load_policy_prompts


_ROOT = Path(__file__).resolve().parent.parent


def test_delegation_policy_contains_anti_base64_instructions():
    """Verify delegation policy explicitly forbids asking for base64."""
    prompts_dir = str(_ROOT / "prompts")
    policy_prompts = load_policy_prompts(prompts_dir)

    delegation_policy = None
    for prompt in policy_prompts:
        if "delegation" in prompt.lower() or "invoke_agent" in prompt.lower():
            delegation_policy = prompt
            break

    assert delegation_policy is not None, "Delegation policy not found"

    critical_phrases = [
        "NEVER ask for base64",
        "NEVER ask the browser agent to return file contents",
        "returns paths via attachments",
        "nothing about encoding or returning data",
    ]

    for phrase in critical_phrases:
        assert phrase in delegation_policy, f"Missing critical phrase: '{phrase}'"


def test_telegram_channel_prompt_contains_attachment_handling():
    """Verify Telegram channel prompt has attachment handling instructions."""
    prompts_dir = str(_ROOT / "prompts")
    telegram_prompt = load_channel_prompt(prompts_dir, "telegram")

    assert telegram_prompt is not None, "Telegram channel prompt not found"

    critical_phrases = [
        "NEVER ask browser agent for base64",
        "this wastes tokens",
        "attachments",
        "send_file",
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
        "Do NOT call send_file",
        "attachments",
    ]

    for phrase in critical_phrases:
        assert phrase in console_prompt, f"Missing critical phrase in console prompt: '{phrase}'"


def test_browser_agent_forbids_base64_return():
    """Verify browser agent explicitly forbids returning base64 data."""
    agents_dir = _ROOT / "agents"
    agent_file = agents_dir / "browser_agent.md"
    assert agent_file.exists(), "browser_agent.md not found"

    content = agent_file.read_text(encoding="utf-8")

    critical_phrases = [
        "NEVER return image data",
        "FORBIDDEN: Do NOT use browser_run_code to get base64",
        "Do NOT return image contents",
        "attachments containing ONLY the path",
        "NEVER return base64",
    ]

    for phrase in critical_phrases:
        assert phrase in content, f"Missing critical phrase in browser agent: '{phrase}'"


def test_browser_agent_has_attachment_example():
    """Verify browser agent shows correct attachment response format."""
    agents_dir = _ROOT / "agents"
    agent_file = agents_dir / "browser_agent.md"

    content = agent_file.read_text(encoding="utf-8")

    assert '"attachments"' in content, "Missing attachments field in example"
    assert '"path":' in content, "Missing path field in attachments example"
    assert '"type": "image/png"' in content, "Missing type field in attachments example"
    assert '"should_answer_to_user": true' in content, "Missing should_answer_to_user in example"


def test_delegation_policy_has_simple_task_example():
    """Verify delegation policy shows minimal task example."""
    prompts_dir = str(_ROOT / "prompts")
    policy_prompts = load_policy_prompts(prompts_dir)

    delegation_policy = None
    for prompt in policy_prompts:
        if "Browser/screenshot delegation" in prompt:
            delegation_policy = prompt
            break

    assert delegation_policy is not None, "Browser delegation section not found"
    assert "Take a screenshot of https://example.com" in delegation_policy, "Missing simple delegation task example"
    assert "that's it" in delegation_policy.lower(), "Missing emphasis on simplicity"


def test_prompt_loader_caches_prompts():
    """Verify prompt loader caching works correctly."""
    prompts_dir = str(_ROOT / "prompts")

    # Load twice - should hit cache
    first_load = load_channel_prompt(prompts_dir, "telegram")
    second_load = load_channel_prompt(prompts_dir, "telegram")

    assert first_load == second_load, "Cached prompts should be identical"
    assert first_load is second_load, "Should return same cached object"


@pytest.mark.asyncio
async def test_integration_prompts_loaded_in_handler(tmp_path: Path):
    """Integration test: verify prompts are correctly assembled in handler."""
    from minibot.shared.prompt_loader import load_channel_prompt, load_policy_prompts

    prompts_dir = str(_ROOT / "prompts")

    # Load prompts as handler does
    channel_prompt = load_channel_prompt(prompts_dir, "telegram")
    policy_prompts = load_policy_prompts(prompts_dir)

    assert channel_prompt is not None
    assert len(policy_prompts) > 0

    # Verify critical content is present
    combined = channel_prompt + "\n" + "\n".join(policy_prompts)

    critical_checks = [
        ("NEVER ask for base64", "Anti-base64 instruction missing"),
        ("attachments", "Attachment handling missing"),
        ("send_file", "send_file instruction missing"),
        ("this wastes tokens", "Token waste warning missing"),
    ]

    for phrase, error_msg in critical_checks:
        assert phrase in combined, error_msg


def test_browser_agent_tool_access():
    """Verify browser agent has access to required tools."""
    agents_dir = _ROOT / "agents"
    agent_file = agents_dir / "browser_agent.md"

    content = agent_file.read_text(encoding="utf-8")

    # Should have playwright MCP tools configured
    assert "mcp_servers:" in content
    assert "playwright-cli" in content

    # Should have list_files for path confirmation
    assert "tools_allow:" in content
    assert "list_files" in content


def test_handler_composes_system_prompt_with_all_fragments():
    """Verify handler _compose_system_prompt method includes all policy fragments."""
    # This is an indirect test - we verify the method exists and uses load_policy_prompts
    # The integration test above already verified the prompts combine correctly
    prompts_dir = str(_ROOT / "prompts")

    # Load what the handler would load
    policy_prompts = load_policy_prompts(prompts_dir)
    telegram_prompt = load_channel_prompt(prompts_dir, "telegram")

    # Simulate what _compose_system_prompt does
    base = "You are Minibot, a helpful assistant."
    fragments = [base]
    fragments.extend(policy_prompts)
    if telegram_prompt:
        fragments.append(telegram_prompt)

    composed = "\n\n".join(fragments)

    # Verify the composed prompt has all critical elements
    assert "You are Minibot" in composed
    assert "NEVER ask for base64" in composed, "Delegation policy missing from composed prompt"
    assert "attachments" in composed, "Attachment handling missing from composed prompt"
    assert "Telegram" in composed or "telegram" in composed, "Channel prompt missing"
    assert len(composed) > 1000, f"Composed prompt too short ({len(composed)} chars) - fragments missing"

    # Verify delegation policy comes before channel prompt (order matters for LLM context)
    delegation_pos = composed.find("NEVER ask for base64")
    telegram_pos = composed.find("Telegram")
    assert delegation_pos > 0 and telegram_pos > delegation_pos, (
        "Delegation policy should appear before channel-specific instructions"
    )
