from __future__ import annotations

from pathlib import Path

from minibot.shared.prompt_loader import load_channel_prompt, load_policy_prompts


def test_load_channel_prompt_reads_channel_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "channels" / "telegram.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("use markdown_v2", encoding="utf-8")

    loaded = load_channel_prompt(str(tmp_path), "telegram")

    assert loaded == "use markdown_v2"


def test_load_channel_prompt_returns_none_for_missing_or_invalid_channel(tmp_path: Path) -> None:
    assert load_channel_prompt(str(tmp_path), "telegram") is None
    assert load_channel_prompt(str(tmp_path), "../telegram") is None


def test_load_policy_prompts_reads_all_policy_files(tmp_path: Path) -> None:
    policies_dir = tmp_path / "policies"
    policies_dir.mkdir(parents=True, exist_ok=True)
    (policies_dir / "a.md").write_text("policy a", encoding="utf-8")
    (policies_dir / "b.md").write_text("policy b", encoding="utf-8")

    loaded = load_policy_prompts(str(tmp_path))

    assert loaded == ["policy a", "policy b"]
