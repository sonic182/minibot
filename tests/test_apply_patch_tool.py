from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from minibot.adapters.config.schema import ApplyPatchToolConfig
from minibot.llm.tools.apply_patch import ApplyPatchTool
from minibot.llm.tools.base import ToolContext


def _binding(config: ApplyPatchToolConfig):
    return {binding.tool.name: binding for binding in ApplyPatchTool(config).bindings()}["apply_patch"]


@pytest.mark.asyncio
async def test_apply_patch_tool_requires_patch_text() -> None:
    binding = _binding(ApplyPatchToolConfig())
    with pytest.raises(ValueError, match="patch_text must be a non-empty string"):
        await binding.handler({"patch_text": ""}, ToolContext())


@pytest.mark.asyncio
async def test_apply_patch_tool_rejects_empty_patch() -> None:
    binding = _binding(ApplyPatchToolConfig())
    with pytest.raises(ValueError, match="patch rejected: empty patch"):
        await binding.handler({"patch_text": "*** Begin Patch\n*** End Patch"}, ToolContext())


@pytest.mark.asyncio
async def test_apply_patch_tool_applies_patch_successfully(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("line1\nline2\n", encoding="utf-8")

    binding = _binding(ApplyPatchToolConfig(workspace_root=str(tmp_path)))
    result = cast(
        dict[str, Any],
        await binding.handler(
            {
                "patch_text": (
                    "*** Begin Patch\n"
                    "*** Add File: nested/new.txt\n"
                    "+created\n"
                    "*** Update File: file.txt\n"
                    "@@\n"
                    "-line2\n"
                    "+changed\n"
                    "*** End Patch"
                )
            },
            ToolContext(),
        ),
    )

    assert result["ok"] is True
    assert "Success. Updated the following files:" in result["summary"]
    assert (tmp_path / "nested/new.txt").read_text(encoding="utf-8") == "created\n"
    assert target.read_text(encoding="utf-8") == "line1\nchanged\n"


@pytest.mark.asyncio
async def test_apply_patch_tool_respects_workspace_restriction(tmp_path: Path) -> None:
    binding = _binding(ApplyPatchToolConfig(workspace_root=str(tmp_path), restrict_to_workspace=True))
    with pytest.raises(ValueError, match="apply_patch verification failed: path escapes workspace root"):
        await binding.handler(
            {"patch_text": "*** Begin Patch\n*** Add File: ../escape.txt\n+x\n*** End Patch"},
            ToolContext(),
        )


@pytest.mark.asyncio
async def test_apply_patch_tool_accepts_unified_hunk_headers(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    binding = _binding(ApplyPatchToolConfig(workspace_root=str(tmp_path)))
    result = cast(
        dict[str, Any],
        await binding.handler(
            {
                "patch_text": (
                    "*** Begin Patch\n"
                    "*** Update File: file.txt\n"
                    "@@ -1,3 +1,3 @@\n"
                    " line1\n"
                    "-line2\n"
                    "+changed\n"
                    " line3\n"
                    "*** End Patch"
                )
            },
            ToolContext(),
        ),
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "line1\nchanged\nline3\n"


@pytest.mark.asyncio
async def test_apply_patch_tool_accumulates_multiple_update_hunks_for_same_file(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("import sys\n\nvalue = 1\nprint(value)\n", encoding="utf-8")

    binding = _binding(ApplyPatchToolConfig(workspace_root=str(tmp_path)))
    result = cast(
        dict[str, Any],
        await binding.handler(
            {
                "patch_text": (
                    "*** Begin Patch\n"
                    "*** Update File: file.txt\n"
                    "@@ -1,4 +1,5 @@\n"
                    " import sys\n"
                    "+import logging\n"
                    " \n"
                    " value = 1\n"
                    " print(value)\n"
                    "*** Update File: file.txt\n"
                    "@@ -2,4 +2,4 @@\n"
                    " import logging\n"
                    " \n"
                    "-value = 1\n"
                    "+value = 2\n"
                    " print(value)\n"
                    "*** End Patch"
                )
            },
            ToolContext(),
        ),
    )

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "import sys\nimport logging\n\nvalue = 2\nprint(value)\n"


@pytest.mark.asyncio
async def test_apply_patch_tool_reports_expected_update_header_forms() -> None:
    binding = _binding(ApplyPatchToolConfig())
    with pytest.raises(ValueError, match=r"Use an update chunk header in one of these forms") as exc_info:
        await binding.handler(
            {"patch_text": ("*** Begin Patch\n*** Update File: file.txt\nnot-a-header\n*** End Patch")},
            ToolContext(),
        )

    assert "@@ -a,b +c,d @@" in str(exc_info.value)
