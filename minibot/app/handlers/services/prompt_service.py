from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from minibot.app.agent_registry import AgentRegistry
from minibot.app.skill_registry import SkillRegistry
from minibot.llm.services import LLMExecutionProfile
from minibot.llm.tools.base import ToolBinding
from minibot.shared.prompt_loader import load_channel_prompt, load_compact_prompt, load_policy_prompts


class PromptService:
    def __init__(
        self,
        llm_client: Any,
        tools: Sequence[ToolBinding],
        environment_prompt_fragment: str,
        logger: logging.Logger,
        agent_registry: AgentRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self._profile = LLMExecutionProfile.from_client(llm_client)
        self._tools = list(tools)
        self._environment_prompt_fragment = environment_prompt_fragment.strip()
        self._logger = logger
        self._prompts_dir = self._profile.prompts_dir
        self._agent_registry = agent_registry
        self._skill_registry = skill_registry

    @property
    def prompts_dir(self) -> str:
        return self._prompts_dir

    def compose_system_prompt(self, channel: str | None) -> str:
        fragments = [self._profile.system_prompt]
        fragments.extend(load_policy_prompts(self._prompts_dir))
        specialist_roster = self._specialist_roster_fragment()
        if specialist_roster:
            fragments.append(specialist_roster)
        skill_catalog = self._skill_catalog_fragment()
        if skill_catalog:
            fragments.append(skill_catalog)
        capability_status = self._capability_status_fragment()
        if capability_status:
            fragments.append(capability_status)
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

    def _capability_status_fragment(self) -> str:
        tool_names = {binding.tool.name for binding in self._tools}
        task_tools_available = {"spawn_task", "cancel_task", "list_tasks"}.issubset(tool_names)
        invoke_agent_available = "invoke_agent" in tool_names
        fetch_agent_info_available = "fetch_agent_info" in tool_names
        specialist_count = 0
        if invoke_agent_available and self._agent_registry is not None and not self._agent_registry.is_empty():
            specialist_count = len(self._agent_registry.names())

        lines = [
            "Runtime capability status:",
            "- Only use tools that are actually attached in this turn.",
            "- If a tool is absent, do not promise or describe using it as if it were available.",
        ]
        for hint in self._profile.provider_capability_hints:
            lines.append(f"- {hint}")
        if task_tools_available:
            lines.append(
                "- Asynchronous delegation is available now via `spawn_task`, with `list_tasks` and `cancel_task` "
                "for tracking and cancellation."
            )
            lines.append(
                "- `spawn_task` can also target a listed specialist via exact `agent_name`"
                " for long-running async work."
            )
            if invoke_agent_available and specialist_count > 0:
                lines.append("- `invoke_agent` is also available as a local fallback when task tools are unsuitable.")
        elif invoke_agent_available and specialist_count > 0:
            lines.append(
                f"- Delegation is available now via `invoke_agent` with {specialist_count} listed specialist agents."
            )
            lines.append("- Prefer delegation for non-trivial specialist work; keep trivial requests local.")
        else:
            lines.append("- Delegation is unavailable in this turn; continue locally with available tools.")
        if fetch_agent_info_available and specialist_count > 0:
            lines.append(
                "- `fetch_agent_info` is available if one listed specialist needs clarification before routing."
            )
        else:
            lines.append("- `fetch_agent_info` is unavailable in this turn.")
        lines.append(
            "- If a needed capability is missing entirely, use the best available alternative or explain the "
            "limitation briefly."
        )
        task_guidance = self._task_worker_guidance_fragment(task_tools_available=task_tools_available)
        if task_guidance:
            lines.append(task_guidance)
        return "\n".join(lines)

    def _specialist_roster_fragment(self) -> str:
        if self._agent_registry is None or self._agent_registry.is_empty():
            return ""
        tool_names = {binding.tool.name for binding in self._tools}
        if "invoke_agent" not in tool_names:
            return ""
        roster = self._agent_registry.prompt_roster()
        if not roster:
            return ""
        if "fetch_agent_info" in tool_names:
            return (
                f"{roster}\n"
                "If you need the full instructions for one specialist before delegating, call fetch_agent_info "
                "with that exact agent name."
            )
        return roster

    def _skill_catalog_fragment(self) -> str:
        if self._skill_registry is None or self._skill_registry.is_empty():
            return ""
        tool_names = {binding.tool.name for binding in self._tools}
        if "activate_skill" not in tool_names:
            return ""
        return self._skill_registry.prompt_catalog()

    def _task_worker_guidance_fragment(self, *, task_tools_available: bool) -> str:
        tool_names = {binding.tool.name for binding in self._tools}
        if not task_tools_available or "list_tasks" not in tool_names:
            return ""
        return "\n".join(
            [
                "Task-worker result handling:",
                '- Messages with `metadata.source == "task_worker"` are asynchronous worker results '
                "from earlier `spawn_task` calls.",
                "- Track pending task ids explicitly. Use `list_tasks` to verify which tasks are "
                "still active.",
                "- When only some task results have arrived, acknowledge the partial completion "
                "briefly and wait for the remaining tasks.",
                "- When all required task results have arrived, synthesize them and continue the "
                "tool loop or answer the user.",
                "- Use `cancel_task` only when the user asks to stop or the remaining work is no "
                "longer useful.",
            ]
        )

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
                "- Do not use placeholder statements.\n\n"
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
            "- Do not use placeholder statements.\n\n"
            f"Original content:\n{original_content}"
        )
