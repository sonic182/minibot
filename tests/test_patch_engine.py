from __future__ import annotations

from pathlib import Path

import pytest

from minibot.llm.tools.patch_engine import (
    apply_patch_actions,
    parse_patch,
    plan_patch_actions,
)


def test_parse_patch_add_update_delete_and_move() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Add File: created.txt\n"
        "+hello\n"
        "*** Update File: a.txt\n"
        "*** Move to: b.txt\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** Delete File: gone.txt\n"
        "*** End Patch"
    )

    parsed = parse_patch(patch)
    assert len(parsed.hunks) == 3
    assert parsed.hunks[0].type == "add"
    assert parsed.hunks[1].type == "update"
    assert parsed.hunks[2].type == "delete"


def test_parse_patch_supports_heredoc_wrapper() -> None:
    patch = (
        "cat <<'EOF'\n"
        "*** Begin Patch\n"
        "*** Add File: test.txt\n"
        "+ok\n"
        "*** End Patch\n"
        "EOF"
    )
    parsed = parse_patch(patch)
    assert len(parsed.hunks) == 1
    assert parsed.hunks[0].type == "add"


def test_parse_patch_rejects_add_lines_without_plus_prefix() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Add File: bad.txt\n"
        "missing-prefix\n"
        "*** End Patch"
    )

    with pytest.raises(ValueError, match="Invalid add line"):
        parse_patch(patch)


def test_parse_patch_rejects_update_hunk_without_header() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.txt\n"
        "not-a-header\n"
        "*** End Patch"
    )

    with pytest.raises(ValueError, match="expected '@@'"):
        parse_patch(patch)


def test_parse_patch_rejects_invalid_update_body_line() -> None:
    patch = (
        "*** Begin Patch\n"
        "*** Update File: a.txt\n"
        "@@\n"
        "bad-body-line\n"
        "*** End Patch"
    )

    with pytest.raises(ValueError, match="Invalid update line"):
        parse_patch(patch)


def test_plan_patch_actions_restricts_escape(tmp_path: Path) -> None:
    parsed = parse_patch("*** Begin Patch\n*** Add File: ../escape.txt\n+no\n*** End Patch")
    with pytest.raises(ValueError, match="path escapes workspace root"):
        plan_patch_actions(
            hunks=parsed.hunks,
            workspace_root=tmp_path,
            restrict_to_workspace=True,
            allow_outside_workspace=False,
            preserve_trailing_newline=True,
        )


def test_plan_and_apply_is_atomic_on_verification_failure(tmp_path: Path) -> None:
    target = tmp_path / "ok.txt"
    target.write_text("line1\nline2\n", encoding="utf-8")
    parsed = parse_patch(
        "*** Begin Patch\n"
        "*** Add File: created.txt\n"
        "+hello\n"
        "*** Update File: missing.txt\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch"
    )

    with pytest.raises(ValueError):
        plan_patch_actions(
            hunks=parsed.hunks,
            workspace_root=tmp_path,
            restrict_to_workspace=True,
            allow_outside_workspace=False,
            preserve_trailing_newline=True,
        )

    assert not (tmp_path / "created.txt").exists()
    assert target.read_text(encoding="utf-8") == "line1\nline2\n"


def test_apply_patch_actions_add_update_delete(tmp_path: Path) -> None:
    modify = tmp_path / "modify.txt"
    delete = tmp_path / "delete.txt"
    modify.write_text("line1\nline2\n", encoding="utf-8")
    delete.write_text("obsolete\n", encoding="utf-8")

    parsed = parse_patch(
        "*** Begin Patch\n"
        "*** Add File: nested/new.txt\n"
        "+created\n"
        "*** Delete File: delete.txt\n"
        "*** Update File: modify.txt\n"
        "@@\n"
        "-line2\n"
        "+changed\n"
        "*** End Patch"
    )

    actions = plan_patch_actions(
        hunks=parsed.hunks,
        workspace_root=tmp_path,
        restrict_to_workspace=True,
        allow_outside_workspace=False,
        preserve_trailing_newline=True,
    )
    result = apply_patch_actions(actions, tmp_path)

    assert (tmp_path / "nested/new.txt").read_text(encoding="utf-8") == "created\n"
    assert modify.read_text(encoding="utf-8") == "line1\nchanged\n"
    assert not delete.exists()
    assert result.summary_lines == ["A nested/new.txt", "D delete.txt", "M modify.txt"]


def test_apply_patch_context_only_addition_respects_matched_location(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("a\nb\nc\n", encoding="utf-8")

    parsed = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: sample.txt\n"
        "@@ b\n"
        "+inserted\n"
        "*** End Patch"
    )

    actions = plan_patch_actions(
        hunks=parsed.hunks,
        workspace_root=tmp_path,
        restrict_to_workspace=True,
        allow_outside_workspace=False,
        preserve_trailing_newline=True,
    )
    apply_patch_actions(actions, tmp_path)

    assert target.read_text(encoding="utf-8") == "a\nb\ninserted\nc\n"


def test_apply_patch_supports_unified_hunk_headers(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    parsed = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: sample.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        "-line2\n"
        "+changed\n"
        " line3\n"
        "*** End Patch"
    )

    actions = plan_patch_actions(
        hunks=parsed.hunks,
        workspace_root=tmp_path,
        restrict_to_workspace=True,
        allow_outside_workspace=False,
        preserve_trailing_newline=True,
    )
    apply_patch_actions(actions, tmp_path)

    assert target.read_text(encoding="utf-8") == "line1\nchanged\nline3\n"


def test_apply_patch_accumulates_multiple_update_hunks_for_same_file(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("import sys\n\nvalue = 1\nprint(value)\n", encoding="utf-8")

    parsed = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: sample.txt\n"
        "@@ -1,4 +1,5 @@\n"
        " import sys\n"
        "+import logging\n"
        " \n"
        " value = 1\n"
        " print(value)\n"
        "*** Update File: sample.txt\n"
        "@@ -2,4 +2,4 @@\n"
        " import logging\n"
        " \n"
        "-value = 1\n"
        "+value = 2\n"
        " print(value)\n"
        "*** End Patch"
    )

    actions = plan_patch_actions(
        hunks=parsed.hunks,
        workspace_root=tmp_path,
        restrict_to_workspace=True,
        allow_outside_workspace=False,
        preserve_trailing_newline=True,
    )
    apply_patch_actions(actions, tmp_path)

    assert target.read_text(encoding="utf-8") == "import sys\nimport logging\n\nvalue = 2\nprint(value)\n"


def test_apply_patch_rejects_ambiguous_unified_hunk_location(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nrepeat\nvalue = 1\nrepeat\nvalue = 1\n", encoding="utf-8")

    parsed = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: sample.txt\n"
        "@@ -3,2 +3,2 @@\n"
        " repeat\n"
        "-value = 1\n"
        "+value = 2\n"
        "*** End Patch"
    )

    with pytest.raises(ValueError, match="Unified hunk location"):
        plan_patch_actions(
            hunks=parsed.hunks,
            workspace_root=tmp_path,
            restrict_to_workspace=True,
            allow_outside_workspace=False,
            preserve_trailing_newline=True,
        )


def test_apply_patch_honors_exact_unified_hunk_offset_for_repeated_blocks(tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nrepeat\nvalue = 1\nrepeat\nvalue = 1\n", encoding="utf-8")

    parsed = parse_patch(
        "*** Begin Patch\n"
        "*** Update File: sample.txt\n"
        "@@ -4,2 +4,2 @@\n"
        " repeat\n"
        "-value = 1\n"
        "+value = 2\n"
        "*** End Patch"
    )

    actions = plan_patch_actions(
        hunks=parsed.hunks,
        workspace_root=tmp_path,
        restrict_to_workspace=True,
        allow_outside_workspace=False,
        preserve_trailing_newline=True,
    )
    apply_patch_actions(actions, tmp_path)

    assert target.read_text(encoding="utf-8") == "alpha\nrepeat\nvalue = 1\nrepeat\nvalue = 2\n"
