from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from minibot.adapters.config.schema import ToolOutputSpillConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.core.agent_runtime import ToolResult
from minibot.llm.services.tool_executor import canonical_tool_name, normalize_tool_result, stringify_result
from minibot.llm.tools.base import ToolBinding, ToolContext, ToolPayload

_READBACK_TOOLS = frozenset({"code_read", "grep", "read_file", "bash"})
_logger = logging.getLogger("minibot.tool_output_spill")


def apply_tool_output_spill(
    bindings: Sequence[ToolBinding],
    *,
    storage: LocalFileStorage | None,
    config: ToolOutputSpillConfig,
) -> list[ToolBinding]:
    """Wrap tool handlers so oversized results are written to a managed file.

    The wrapper is only applied when the binding set also exposes a tool capable of
    reading the file back (``grep``, ``code_read``, ``read_file`` or ``bash``);
    otherwise the pointer would be unusable and results stay inline.
    """
    if not config.enabled or storage is None:
        return list(bindings)

    names = {canonical_tool_name(binding.tool.name) for binding in bindings}
    readback = sorted(names & _READBACK_TOOLS)
    if not readback:
        return list(bindings)

    excluded = {canonical_tool_name(name) for name in config.exclude_tools}
    return [
        binding
        if canonical_tool_name(binding.tool.name) in excluded
        else _wrap(binding, storage=storage, config=config, readback=readback)
        for binding in bindings
    ]


def _wrap(
    binding: ToolBinding,
    *,
    storage: LocalFileStorage,
    config: ToolOutputSpillConfig,
    readback: list[str],
) -> ToolBinding:
    tool_name = canonical_tool_name(binding.tool.name)

    async def handler(payload: ToolPayload, context: ToolContext) -> ToolResult:
        result = normalize_tool_result(await binding.handler(payload, context))
        content = result.content
        if _is_error(content):
            return result
        text = stringify_result(content)
        if len(text) <= config.spill_after_chars:
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
            return result

        return ToolResult(
            content={
                "ok": True,
                "tool": tool_name,
                "output_storage": "managed_file",
                "output_file_path": saved["path"],
                "output_file_absolute_path": saved["absolute_path"],
                "output_bytes_written": saved["bytes_written"],
                "output_preview": text[: config.preview_chars],
                "output_notice": (
                    f"Output exceeded {config.spill_after_chars} chars and was saved to a managed file. "
                    f"Use {' or '.join(readback)} on output_file_path to inspect the full output."
                ),
            },
            directives=result.directives,
        )

    return ToolBinding(tool=binding.tool, handler=handler)


def _is_error(content: Any) -> bool:
    return isinstance(content, dict) and content.get("ok") is False
