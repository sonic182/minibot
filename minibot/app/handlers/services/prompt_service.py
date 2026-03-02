from __future__ import annotations

import logging
from typing import Sequence

from minibot.llm.provider_factory import LLMClient
from minibot.llm.tools.base import ToolBinding
from minibot.shared.prompt_loader import load_channel_prompt, load_compact_prompt, load_policy_prompts


class PromptService:
    def __init__(
        self,
        llm_client: LLMClient,
        tools: Sequence[ToolBinding],
        environment_prompt_fragment: str,
        logger: logging.Logger,
    ) -> None:
        self._llm_client = llm_client
        self._tools = list(tools)
        self._environment_prompt_fragment = environment_prompt_fragment.strip()
        self._logger = logger
        self._prompts_dir = self._resolve_prompts_dir()

    @property
    def prompts_dir(self) -> str:
        return self._prompts_dir

    def compose_system_prompt(self, channel: str | None) -> str:
        system_prompt_getter = getattr(self._llm_client, "system_prompt", None)
        base_prompt = "You are Minibot, a helpful assistant."
        if callable(system_prompt_getter):
            maybe_prompt = system_prompt_getter()
            if isinstance(maybe_prompt, str) and maybe_prompt:
                base_prompt = maybe_prompt

        fragments = [base_prompt]
        fragments.extend(load_policy_prompts(self._prompts_dir))
        channel_prompt = load_channel_prompt(self._prompts_dir, channel)
        if channel_prompt:
            fragments.append(channel_prompt)
        if self._environment_prompt_fragment:
            fragments.append(self._environment_prompt_fragment)
        self._logger.debug(
            "composed system prompt",
            extra={
                "channel": channel,
                "prompts_dir": self._prompts_dir,
                "channel_prompt_loaded": bool(channel_prompt),
                "fragment_count": len(fragments),
                "prompt_preview": "\n\n".join(fragments)[:200],
            },
        )

        if any(binding.tool.name == "self_insert_artifact" for binding in self._tools):
            fragments.append(
                "When you need to inspect a local workspace file (image/document), call self_insert_artifact first "
                "to inject it into conversation context before answering file contents. "
                "For file-management requests (save, move, delete, send, list), do not call self_insert_artifact; "
                "use the filesystem tool with the appropriate action instead. "
                "If the user only uploaded files and gave no clear instruction, ask a clarifying question."
            )
        return "\n\n".join(fragments)

    def compact_system_prompt(self, system_prompt: str) -> str:
        compact_prompt = load_compact_prompt(self._prompts_dir)
        if compact_prompt:
            return f"{system_prompt}\n\n{compact_prompt}"
        return (
            f"{system_prompt}\n\n"
            "You are compacting conversation memory. Return a concise but complete summary of the "
            "conversation so far, preserving user goals, constraints, and pending tasks. "
            "Do not include preamble."
        )

    @staticmethod
    def build_format_repair_prompt(
        *,
        channel: str,
        original_kind: str,
        parse_error: str,
        original_content: str,
    ) -> str:
        if channel == "telegram":
            return (
                "We tried to send a formatted response to Telegram but got a formatting parse error. "
                "Rewrite the same answer with valid Telegram-compatible formatting.\n\n"
                f"Original kind: {original_kind}\n"
                f"Telegram error: {parse_error}\n\n"
                "Requirements:\n"
                "- Return the same meaning and content, only fix formatting.\n"
                "- Keep kind as markdown or html only if valid for Telegram, otherwise use text.\n"
                "- For markdown, write normal Markdown (do not pre-escape Telegram MarkdownV2).\n"
                "- Do not use placeholder statements.\n"
                "- Return structured output only.\n\n"
                f"Original content:\n{original_content}"
            )
        return (
            "We tried to send a formatted response to the target channel and got a formatting parse error. "
            "Rewrite the same answer with valid channel-compatible formatting.\n\n"
            f"Channel: {channel}\n"
            f"Original kind: {original_kind}\n"
            f"Parse error: {parse_error}\n\n"
            "Requirements:\n"
            "- Return the same meaning and content, only fix formatting.\n"
            "- Keep kind aligned with valid formatting for this channel, otherwise use text.\n"
            "- Do not use placeholder statements.\n"
            "- Return structured output only.\n\n"
            f"Original content:\n{original_content}"
        )

    def _resolve_prompts_dir(self) -> str:
        prompts_dir_getter = getattr(self._llm_client, "prompts_dir", None)
        if callable(prompts_dir_getter):
            value = prompts_dir_getter()
            if isinstance(value, str) and value:
                return value
        return "./prompts"
