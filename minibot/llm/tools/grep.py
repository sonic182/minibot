from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from llm_async.models import Tool

from minibot.adapters.config.schema import GrepToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.llm.tools.arg_utils import optional_bool, optional_int, optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_boolean, nullable_integer, nullable_string, strict_object


class GrepTool:
    def __init__(self, storage: LocalFileStorage, config: GrepToolConfig) -> None:
        self._storage = storage
        self._config = config

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        return Tool(
            name="grep",
            description=load_tool_description("grep"),
            parameters=strict_object(
                properties={
                    "pattern": {"type": "string", "description": "Regex or fixed pattern to search."},
                    "path": nullable_string("Optional file/folder path. Defaults to current managed root."),
                    "recursive": nullable_boolean("Search folders recursively. Defaults to true."),
                    "ignore_case": nullable_boolean("Case-insensitive matching."),
                    "fixed_string": nullable_boolean("Treat pattern as a literal string."),
                    "include_hidden": nullable_boolean("Include hidden files/folders."),
                    "context_before": nullable_integer(minimum=0, description="Lines of context before match."),
                    "context_after": nullable_integer(minimum=0, description="Lines of context after match."),
                    "max_matches": nullable_integer(minimum=1, description="Optional per-call match cap."),
                },
                required=[
                    "pattern",
                    "path",
                    "recursive",
                    "ignore_case",
                    "fixed_string",
                    "include_hidden",
                    "context_before",
                    "context_after",
                    "max_matches",
                ],
            ),
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        pattern = require_non_empty_str(payload, "pattern")
        raw_path = optional_str(payload.get("path"))
        recursive = optional_bool(
            payload.get("recursive"),
            default=True,
            error_message="recursive must be a boolean or null",
        )
        ignore_case = optional_bool(
            payload.get("ignore_case"),
            default=False,
            error_message="ignore_case must be a boolean or null",
        )
        fixed_string = optional_bool(
            payload.get("fixed_string"),
            default=False,
            error_message="fixed_string must be a boolean or null",
        )
        include_hidden = optional_bool(
            payload.get("include_hidden"),
            default=False,
            error_message="include_hidden must be a boolean or null",
        )
        context_before = optional_int(
            payload.get("context_before"),
            field="context_before",
            min_value=0,
            allow_float=False,
            allow_string=False,
            type_error="context_before must be an integer >= 0",
            min_error="context_before must be an integer >= 0",
        )
        context_after = optional_int(
            payload.get("context_after"),
            field="context_after",
            min_value=0,
            allow_float=False,
            allow_string=False,
            type_error="context_after must be an integer >= 0",
            min_error="context_after must be an integer >= 0",
        )
        max_matches = optional_int(
            payload.get("max_matches"),
            field="max_matches",
            min_value=1,
            allow_float=False,
            allow_string=False,
            type_error="max_matches must be an integer >= 1",
            min_error="max_matches must be an integer >= 1",
        )
        effective_max_matches = max_matches or self._config.max_matches

        target = self._resolve_target(raw_path)
        matcher = self._build_matcher(pattern=pattern, ignore_case=ignore_case, fixed_string=fixed_string)

        files = self._iter_candidate_files(target, recursive=recursive, include_hidden=include_hidden)
        matches: list[dict[str, Any]] = []
        files_scanned = 0
        files_skipped = 0
        truncated = False
        before = context_before or 0
        after = context_after or 0

        for file_path in files:
            files_scanned += 1
            try:
                stat = file_path.stat()
            except OSError:
                files_skipped += 1
                continue
            if stat.st_size > self._config.max_file_size_bytes:
                files_skipped += 1
                continue
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                files_skipped += 1
                continue
            for index, line in enumerate(lines):
                if not matcher(line):
                    continue
                start = max(0, index - before)
                end = min(len(lines), index + after + 1)
                matches.append(
                    {
                        "path": self._storage.display_path(file_path),
                        "line": index + 1,
                        "match": line,
                        "context_before": lines[start:index],
                        "context_after": lines[index + 1 : end],
                    }
                )
                if len(matches) >= effective_max_matches:
                    truncated = True
                    break
            if truncated:
                break

        return {
            "ok": True,
            "pattern": pattern,
            "path": raw_path or ".",
            "recursive": recursive,
            "ignore_case": ignore_case,
            "fixed_string": fixed_string,
            "include_hidden": include_hidden,
            "context_before": before,
            "context_after": after,
            "max_matches": effective_max_matches,
            "max_file_size_bytes": self._config.max_file_size_bytes,
            "count": len(matches),
            "files_scanned": files_scanned,
            "files_skipped": files_skipped,
            "truncated": truncated,
            "matches": matches,
        }

    def _resolve_target(self, path_value: str | None) -> Path:
        if not path_value:
            return self._storage.resolve_dir(".")
        resolved = self._storage.resolve_file(path_value)
        if not resolved.exists():
            raise ValueError("path does not exist")
        return resolved

    def _iter_candidate_files(self, target: Path, *, recursive: bool, include_hidden: bool) -> list[Path]:
        if target.is_file():
            return [target]

        files = target.rglob("*") if recursive else target.glob("*")
        collected: list[Path] = []
        for candidate in files:
            if not candidate.is_file():
                continue
            if not include_hidden and self._is_hidden(candidate, base=target):
                continue
            collected.append(candidate)
        return sorted(collected, key=lambda item: self._storage.display_path(item).lower())

    @staticmethod
    def _is_hidden(path: Path, *, base: Path) -> bool:
        try:
            relative_parts = path.relative_to(base).parts
        except ValueError:
            relative_parts = path.parts
        return any(part.startswith(".") for part in relative_parts)

    def _build_matcher(self, *, pattern: str, ignore_case: bool, fixed_string: bool) -> Any:
        if fixed_string:
            needle = pattern.lower() if ignore_case else pattern

            def _matches_fixed(line: str) -> bool:
                haystack = line.lower() if ignore_case else line
                return needle in haystack

            return _matches_fixed

        flags = re.IGNORECASE if ignore_case else 0
        try:
            compiled = re.compile(pattern, flags=flags)
        except re.error as exc:
            raise ValueError(f"invalid regex pattern: {exc}") from exc
        return compiled.search
