from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=64)
def _load_channel_prompt_cached(prompts_dir: str, channel: str) -> str | None:
    channel_name = channel.strip().lower()
    if not channel_name:
        return None
    if not all(character.isalnum() or character in {"_", "-"} for character in channel_name):
        return None
    prompt_path = Path(prompts_dir) / "channels" / f"{channel_name}.md"
    if not prompt_path.exists() or not prompt_path.is_file():
        return None
    content = prompt_path.read_text(encoding="utf-8").strip()
    return content or None


def load_channel_prompt(prompts_dir: str, channel: str | None) -> str | None:
    if not channel:
        return None
    return _load_channel_prompt_cached(prompts_dir, channel)


@lru_cache(maxsize=32)
def _load_policy_prompts_cached(prompts_dir: str) -> tuple[str, ...]:
    policies_dir = Path(prompts_dir) / "policies"
    if not policies_dir.exists() or not policies_dir.is_dir():
        return ()
    prompts: list[str] = []
    for path in sorted(policies_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        if content:
            prompts.append(content)
    return tuple(prompts)


def load_policy_prompts(prompts_dir: str) -> list[str]:
    return list(_load_policy_prompts_cached(prompts_dir))


@lru_cache(maxsize=32)
def _load_compact_prompt_cached(prompts_dir: str) -> str | None:
    prompt_path = Path(prompts_dir) / "compact.md"
    if not prompt_path.exists() or not prompt_path.is_file():
        return None
    content = prompt_path.read_text(encoding="utf-8").strip()
    return content or None


def load_compact_prompt(prompts_dir: str) -> str | None:
    return _load_compact_prompt_cached(prompts_dir)
