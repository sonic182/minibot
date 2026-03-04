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
