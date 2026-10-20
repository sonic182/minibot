from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from minibot.config.schema import ToolOutputSpillConfig
from minibot.core.agent_runtime import ToolResult
from minibot.core.files import FileStorage
from minibot.llm.services.tool_executor import canonical_tool_name, normalize_tool_result, stringify_result
from minibot.llm.tools.base import ToolBinding, ToolContext, ToolPayload

_READBACK_TOOLS = frozenset({"code_read", "grep", "read_file", "bash"})
# Skill instructions are the point of the call: a pointer to them makes the agent spend its tool
# calls reading the file back instead of doing the task. They stay inline up to this size; above
# it they spill like anything else, so one huge skill cannot ride along in every later step.
_SKILL_SPILL_AFTER_CHARS = 64_000
# A spilled failure keeps its short top-level fields (error codes, the runtime's repeated-failure signature).
_KEPT_FIELD_MAX_CHARS = 200
_logger = logging.getLogger("minibot.tool_output_spill")


def apply_tool_output_spill(
    bindings: Sequence[ToolBinding],
    *,
    storage: FileStorage | None,
    config: ToolOutputSpillConfig,
) -> list[ToolBinding]:
    """Wrap tool handlers so oversized results, failed ones included, are written to a managed file.

    The wrapper is only applied when the binding set also exposes a tool capable of
    reading the file back (``grep``, ``code_read``, ``read_file`` or ``bash``);
    otherwise the pointer would be unusable and results stay inline. A binding's own
    ``spill_after_chars`` raises its threshold; where spill cannot run, such a binding's
    results are cut to it instead.
    """
    if not config.enabled or storage is None:
        return [_bounded(binding) for binding in bindings]

    names = {canonical_tool_name(binding.tool.name) for binding in bindings}
    readback = sorted(names & _READBACK_TOOLS)
    if not readback:
        return [_bounded(binding) for binding in bindings]

    excluded = {canonical_tool_name(name) for name in config.exclude_tools}
    skill_config = config.model_copy(
        update={"spill_after_chars": max(config.spill_after_chars, _SKILL_SPILL_AFTER_CHARS)}
    )
    return [
        binding
        if canonical_tool_name(binding.tool.name) in excluded
        else _wrap(
            binding,
            storage=storage,
            config=skill_config if canonical_tool_name(binding.tool.name) == "activate_skill" else config,
            readback=readback,
        )
        for binding in bindings
    ]


def _wrap(
    binding: ToolBinding,
    *,
    storage: FileStorage,
    config: ToolOutputSpillConfig,
    readback: list[str],
) -> ToolBinding:
    tool_name = canonical_tool_name(binding.tool.name)
    spill_after_chars = max(config.spill_after_chars, binding.spill_after_chars or 0)

    async def handler(payload: ToolPayload, context: ToolContext) -> ToolResult:
        result = normalize_tool_result(await binding.handler(payload, context))
        # Nested in a codemode script: the result goes to the script, not the model's context, and the
        # script's own output limits protect the context.
        if context.parent_call_id is not None:
            return result
        content = result.content
        text = stringify_result(content)
        if len(text) <= spill_after_chars:
            return result

        try:
            saved = storage.create_managed_temp_text_file(
                subdir=config.subdir,
                stem=f"tool-{tool_name}",
                suffix=".json" if isinstance(content, (dict, list)) else ".txt",
                content=text,
            )
        except Exception:  # noqa: BLE001
            _logger.warning(
                "tool output spill write failed; returning result inline",
                extra={"tool": tool_name, "subdir": config.subdir},
                exc_info=True,
            )
            return _cut(result, tool_name, binding.spill_after_chars)

        return ToolResult(
            content={
                **_short_fields(content),
                "ok": not _is_error(content),
                "tool": tool_name,
                "output_storage": "managed_file",
                "output_file_path": saved["path"],
                "output_file_absolute_path": saved["absolute_path"],
                "output_bytes_written": saved["bytes_written"],
                "output_preview": text[: config.preview_chars],
                "output_notice": (
                    f"Output exceeded {spill_after_chars} chars and was saved to a managed file. "
                    f"Use {' or '.join(readback)} on output_file_path to inspect the full output."
                ),
            },
            directives=result.directives,
        )

    return ToolBinding(tool=binding.tool, handler=handler)


def _bounded(binding: ToolBinding) -> ToolBinding:
    """Where spill cannot run, cut the results of a binding that declares ``spill_after_chars``."""
    limit = binding.spill_after_chars
    if limit is None:
        return binding
    tool_name = canonical_tool_name(binding.tool.name)

    async def handler(payload: ToolPayload, context: ToolContext) -> ToolResult:
        result = normalize_tool_result(await binding.handler(payload, context))
        if context.parent_call_id is not None:
            return result
        return _cut(result, tool_name, limit)

    return ToolBinding(tool=binding.tool, handler=handler)


def _cut(result: ToolResult, tool_name: str, limit: int | None) -> ToolResult:
    if limit is None:
        return result
    text = stringify_result(result.content)
    if len(text) <= limit:
        return result
    return ToolResult(
        content={
            "ok": not _is_error(result.content),
            "tool": tool_name,
            "output_truncated": True,
            "output_preview": text[:limit],
            "output_notice": (
                f"Output was {len(text)} chars; only the first {limit} are shown because no managed file "
                "was available to save it."
            ),
        },
        directives=result.directives,
    )


def _short_fields(content: Any) -> dict[str, Any]:
    if not _is_error(content):
        return {}
    return {
        key: value
        for key, value in content.items()
        if value is None
        or isinstance(value, bool | int | float)
        or (isinstance(value, str) and len(value) <= _KEPT_FIELD_MAX_CHARS)
    }


def _is_error(content: Any) -> bool:
    return isinstance(content, dict) and content.get("ok") is False
