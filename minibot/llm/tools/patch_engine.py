from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class UpdateFileChunk:
    old_lines: list[str]
    new_lines: list[str]
    change_context: str | None = None
    is_end_of_file: bool = False


@dataclass(frozen=True)
class AddHunk:
    type: Literal["add"]
    path: str
    contents: str


@dataclass(frozen=True)
class DeleteHunk:
    type: Literal["delete"]
    path: str


@dataclass(frozen=True)
class UpdateHunk:
    type: Literal["update"]
    path: str
    move_path: str | None
    chunks: list[UpdateFileChunk]


PatchHunk = AddHunk | DeleteHunk | UpdateHunk


@dataclass(frozen=True)
class PatchParseResult:
    hunks: list[PatchHunk]


@dataclass(frozen=True)
class AddAction:
    type: Literal["add"]
    target: Path
    contents: str


@dataclass(frozen=True)
class DeleteAction:
    type: Literal["delete"]
    target: Path


@dataclass(frozen=True)
class UpdateAction:
    type: Literal["update"]
    source: Path
    target: Path
    contents: str
    moved: bool


PatchAction = AddAction | DeleteAction | UpdateAction


@dataclass(frozen=True)
class ApplyResult:
    summary_lines: list[str]


_HEREDOC_RE = re.compile(r"^(?:cat\s+)?<<['\"]?(\w+)['\"]?\s*\n([\s\S]*?)\n\1\s*$")
_UNIFIED_HUNK_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_count>\d+))?\s+\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?\s+@@(?:\s+(?P<context>.*))?$"
)


def strip_heredoc(text: str) -> str:
    match = _HEREDOC_RE.match(text.strip())
    if match:
        return match.group(2)
    return text


def parse_patch(patch_text: str) -> PatchParseResult:
    cleaned = strip_heredoc(patch_text.strip())
    lines = cleaned.split("\n")
    begin_idx = _find_line(lines, "*** Begin Patch")
    end_idx = _find_line(lines, "*** End Patch")
    if begin_idx == -1 or end_idx == -1 or begin_idx >= end_idx:
        raise ValueError("Invalid patch format: missing Begin/End markers")

    hunks: list[PatchHunk] = []
    i = begin_idx + 1
    while i < end_idx:
        line = lines[i]
        if line.startswith("*** Add File:"):
            file_path = line[len("*** Add File:") :].strip()
            if not file_path:
                raise ValueError("Invalid add header")
            i += 1
            content_lines: list[str] = []
            while i < end_idx and not lines[i].startswith("***"):
                current = lines[i]
                if not current.startswith("+"):
                    raise ValueError(f"Invalid add line for {file_path}: expected '+' prefix")
                content_lines.append(current[1:])
                i += 1
            hunks.append(AddHunk(type="add", path=file_path, contents="\n".join(content_lines)))
            continue

        if line.startswith("*** Delete File:"):
            file_path = line[len("*** Delete File:") :].strip()
            if not file_path:
                raise ValueError("Invalid delete header")
            hunks.append(DeleteHunk(type="delete", path=file_path))
            i += 1
            continue

        if line.startswith("*** Update File:"):
            file_path = line[len("*** Update File:") :].strip()
            if not file_path:
                raise ValueError("Invalid update header")
            i += 1
            move_path: str | None = None
            if i < end_idx and lines[i].startswith("*** Move to:"):
                move_path = lines[i][len("*** Move to:") :].strip() or None
                i += 1

            chunks: list[UpdateFileChunk] = []
            while i < end_idx and not lines[i].startswith("***"):
                if not lines[i].startswith("@@"):
                    raise ValueError(
                        f"Invalid update hunk for {file_path}: expected '@@', '@@ <context>', or '@@ -a,b +c,d @@'"
                    )
                context = _parse_update_hunk_header(lines[i], file_path)
                i += 1
                old_lines: list[str] = []
                new_lines: list[str] = []
                eof_anchor = False
                while i < end_idx and not lines[i].startswith("@@") and not lines[i].startswith("***"):
                    raw = lines[i]
                    if raw == "*** End of File":
                        eof_anchor = True
                        i += 1
                        break
                    if raw.startswith(" "):
                        old_lines.append(raw[1:])
                        new_lines.append(raw[1:])
                    elif raw.startswith("-"):
                        old_lines.append(raw[1:])
                    elif raw.startswith("+"):
                        new_lines.append(raw[1:])
                    else:
                        raise ValueError(
                            f"Invalid update line for {file_path}: expected one of ' ', '+', '-', or '*** End of File'"
                        )
                    i += 1
                chunks.append(
                    UpdateFileChunk(
                        old_lines=old_lines,
                        new_lines=new_lines,
                        change_context=context,
                        is_end_of_file=eof_anchor,
                    )
                )

            hunks.append(UpdateHunk(type="update", path=file_path, move_path=move_path, chunks=chunks))
            continue

        i += 1

    return PatchParseResult(hunks=hunks)


def plan_patch_actions(
    *,
    hunks: list[PatchHunk],
    workspace_root: Path,
    restrict_to_workspace: bool,
    allow_outside_workspace: bool,
    preserve_trailing_newline: bool,
) -> list[PatchAction]:
    actions: list[PatchAction] = []

    for hunk in hunks:
        if hunk.type == "add":
            target = _resolve_path(
                hunk.path,
                workspace_root=workspace_root,
                restrict_to_workspace=restrict_to_workspace,
                allow_outside_workspace=allow_outside_workspace,
            )
            actions.append(AddAction(type="add", target=target, contents=_with_newline(hunk.contents)))
            continue

        if hunk.type == "delete":
            target = _resolve_path(
                hunk.path,
                workspace_root=workspace_root,
                restrict_to_workspace=restrict_to_workspace,
                allow_outside_workspace=allow_outside_workspace,
            )
            if not target.exists() or not target.is_file():
                raise ValueError(f"Failed to read file to delete: {target}")
            actions.append(DeleteAction(type="delete", target=target))
            continue

        source = _resolve_path(
            hunk.path,
            workspace_root=workspace_root,
            restrict_to_workspace=restrict_to_workspace,
            allow_outside_workspace=allow_outside_workspace,
        )
        if not source.exists() or not source.is_file():
            raise ValueError(f"Failed to read file to update: {source}")

        original_content = source.read_text(encoding="utf-8")
        new_content = derive_new_contents_from_chunks(source, hunk.chunks, original_content=original_content)
        if preserve_trailing_newline:
            new_content = _with_newline(new_content)

        target = source
        moved = False
        if hunk.move_path:
            target = _resolve_path(
                hunk.move_path,
                workspace_root=workspace_root,
                restrict_to_workspace=restrict_to_workspace,
                allow_outside_workspace=allow_outside_workspace,
            )
            moved = True

        actions.append(UpdateAction(type="update", source=source, target=target, contents=new_content, moved=moved))

    return actions


def apply_patch_actions(actions: list[PatchAction], workspace_root: Path) -> ApplyResult:
    summary_lines: list[str] = []
    for action in actions:
        if action.type == "add":
            action.target.parent.mkdir(parents=True, exist_ok=True)
            action.target.write_text(action.contents, encoding="utf-8")
            summary_lines.append(f"A {_rel(action.target, workspace_root)}")
            continue

        if action.type == "delete":
            action.target.unlink()
            summary_lines.append(f"D {_rel(action.target, workspace_root)}")
            continue

        action.target.parent.mkdir(parents=True, exist_ok=True)
        action.target.write_text(action.contents, encoding="utf-8")
        if action.moved and action.source != action.target and action.source.exists():
            action.source.unlink()
        summary_lines.append(f"M {_rel(action.target, workspace_root)}")

    return ApplyResult(summary_lines=summary_lines)


def derive_new_contents_from_chunks(file_path: Path, chunks: list[UpdateFileChunk], *, original_content: str) -> str:
    original_lines = original_content.split("\n")
    if original_lines and original_lines[-1] == "":
        original_lines.pop()

    replacements = _compute_replacements(original_lines, file_path, chunks)
    new_lines = _apply_replacements(original_lines, replacements)
    return "\n".join(new_lines)


def _compute_replacements(
    original_lines: list[str],
    file_path: Path,
    chunks: list[UpdateFileChunk],
) -> list[tuple[int, int, list[str]]]:
    replacements: list[tuple[int, int, list[str]]] = []
    line_index = 0

    for chunk in chunks:
        if chunk.change_context:
            context_idx = _seek_sequence(original_lines, [chunk.change_context], line_index, eof=False)
            if context_idx == -1:
                raise ValueError(f"Failed to find context '{chunk.change_context}' in {file_path}")
            line_index = context_idx + 1

        if not chunk.old_lines:
            insertion_idx = line_index
            replacements.append((insertion_idx, 0, chunk.new_lines))
            continue

        pattern = list(chunk.old_lines)
        new_slice = list(chunk.new_lines)
        found = _seek_sequence(original_lines, pattern, line_index, eof=chunk.is_end_of_file)
        if found == -1 and pattern and pattern[-1] == "":
            pattern = pattern[:-1]
            if new_slice and new_slice[-1] == "":
                new_slice = new_slice[:-1]
            found = _seek_sequence(original_lines, pattern, line_index, eof=chunk.is_end_of_file)

        if found == -1:
            raise ValueError(f"Failed to find expected lines in {file_path}:\n" + "\n".join(chunk.old_lines))

        replacements.append((found, len(pattern), new_slice))
        line_index = found + len(pattern)

    replacements.sort(key=lambda item: item[0])
    return replacements


def _apply_replacements(lines: list[str], replacements: list[tuple[int, int, list[str]]]) -> list[str]:
    result = list(lines)
    for start, old_len, new_segment in reversed(replacements):
        del result[start : start + old_len]
        for idx, line in enumerate(new_segment):
            result.insert(start + idx, line)
    return result


def _seek_sequence(lines: list[str], pattern: list[str], start_index: int, *, eof: bool) -> int:
    if not pattern:
        return -1

    comparators = (
        lambda a, b: a == b,
        lambda a, b: a.rstrip() == b.rstrip(),
        lambda a, b: a.strip() == b.strip(),
        lambda a, b: _normalize_unicode(a.strip()) == _normalize_unicode(b.strip()),
    )

    for compare in comparators:
        found = _try_match(lines, pattern, start_index, compare, eof=eof)
        if found != -1:
            return found
    return -1


def _try_match(lines: list[str], pattern: list[str], start_index: int, compare, *, eof: bool) -> int:
    if eof:
        from_end = len(lines) - len(pattern)
        if from_end >= start_index:
            if all(compare(lines[from_end + j], pattern[j]) for j in range(len(pattern))):
                return from_end

    max_start = len(lines) - len(pattern)
    for i in range(start_index, max_start + 1):
        if all(compare(lines[i + j], pattern[j]) for j in range(len(pattern))):
            return i
    return -1


def _normalize_unicode(value: str) -> str:
    return (
        value.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201A", "'")
        .replace("\u201B", "'")
        .replace("\u201C", '"')
        .replace("\u201D", '"')
        .replace("\u201E", '"')
        .replace("\u201F", '"')
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2015", "-")
        .replace("\u2026", "...")
        .replace("\u00A0", " ")
    )


def _resolve_path(
    path_str: str,
    *,
    workspace_root: Path,
    restrict_to_workspace: bool,
    allow_outside_workspace: bool,
) -> Path:
    candidate = Path(path_str)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (workspace_root / candidate).resolve()

    if restrict_to_workspace and not allow_outside_workspace:
        root = workspace_root.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"path escapes workspace root: {path_str}") from exc

    return resolved


def _with_newline(content: str) -> str:
    if not content:
        return "\n"
    return content if content.endswith("\n") else f"{content}\n"


def _rel(path: Path, workspace_root: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _find_line(lines: list[str], marker: str) -> int:
    for index, line in enumerate(lines):
        if line.strip() == marker:
            return index
    return -1


def _parse_update_hunk_header(line: str, file_path: str) -> str | None:
    stripped = line.strip()
    if stripped == "@@":
        return None

    unified_match = _UNIFIED_HUNK_RE.fullmatch(stripped)
    if unified_match is not None:
        context = unified_match.group("context")
        if context is None:
            return None
        return context.strip() or None

    if stripped.startswith("@@ "):
        context = stripped[2:].strip()
        if context:
            return context

    raise ValueError(
        f"Invalid update hunk for {file_path}: expected '@@', '@@ <context>', or '@@ -a,b +c,d @@'"
    )
