from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path
from typing import Any

from llm_async.models import Tool

from minibot.llm.tools.arg_utils import optional_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_string, strict_object

_PAGE_MAX_CHARS = 30000
_MAX_HITS = 8
_MAX_SNIPPETS = 3
_SNIPPET_MAX_CHARS = 200
_SUFFIXES = frozenset({".rst", ".md"})
_AUTOCLASS = re.compile(r"^\.\. autoclass:: (minibot\.[\w.]+)\s*$")
_DIRECTIVE_OPTION = re.compile(r"^\s+:[\w-]+:")
_UNDERLINE = re.compile(r"^([=\-~^\"#*`])\1{2,}\s*$")
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def docs_root() -> Path | None:
    repo_docs = _PACKAGE_ROOT.parent / "docs" if (_PACKAGE_ROOT.parent / "pyproject.toml").is_file() else None
    for candidate in (_PACKAGE_ROOT / "_docs", repo_docs):
        if (
            candidate is not None
            and candidate.is_dir()
            and any(path.suffix in _SUFFIXES for path in candidate.iterdir())
        ):
            return candidate
    return None


class DocsReaderTool:
    """Read-only access to the documentation that ships with this MiniBot version.

    Pages are addressed by name from a fixed listing, never by a path built from the input.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    def bindings(self) -> list[ToolBinding]:
        schema = Tool(
            name="read_docs",
            description=load_tool_description("read_docs"),
            parameters=strict_object(
                properties={
                    "page": nullable_string("Page name from the listing, for example 'vault'."),
                    "query": nullable_string("Words to search for across every page."),
                },
                required=["page", "query"],
            ),
        )
        return [ToolBinding(tool=schema, handler=self._handle)]

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        root = self._root or docs_root()
        if root is None or not root.is_dir():
            return {"ok": False, "error_code": "docs_unavailable", "error": "no documentation ships with this install"}
        pages = {path.stem: path for path in sorted(root.iterdir()) if path.suffix in _SUFFIXES}
        page = optional_str(payload.get("page"), error_message="page must be a string")
        query = optional_str(payload.get("query"), error_message="query must be a string")
        if page is not None:
            return self._read(pages, page)
        if query is not None:
            return self._search(pages, query)
        return {
            "ok": True,
            "pages": [
                {"page": name, "title": _title(path.read_text(encoding="utf-8"), name)} for name, path in pages.items()
            ],
        }

    def _read(self, pages: dict[str, Path], page: str) -> dict[str, Any]:
        path = pages.get(page)
        if path is None:
            return {
                "ok": False,
                "error_code": "page_not_found",
                "error": f"no page named '{page}'",
                "pages": list(pages),
            }
        text = _expand_autoclass(path.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "page": page,
            "title": _title(text, page),
            "content": text[:_PAGE_MAX_CHARS],
            "truncated": len(text) > _PAGE_MAX_CHARS,
        }

    def _search(self, pages: dict[str, Path], query: str) -> dict[str, Any]:
        terms = query.casefold().split()
        hits: list[dict[str, Any]] = []
        for name, path in pages.items():
            lines = _expand_autoclass(path.read_text(encoding="utf-8")).splitlines()
            matching = [line.strip() for line in lines if all(term in line.casefold() for term in terms)]
            if matching:
                snippets = [line[:_SNIPPET_MAX_CHARS] for line in matching[:_MAX_SNIPPETS]]
                hits.append({"page": name, "matches": len(matching), "snippets": snippets})
        hits.sort(key=lambda hit: hit["matches"], reverse=True)
        return {"ok": True, "query": query, "results": hits[:_MAX_HITS]}


def _title(text: str, fallback: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if stripped and not _UNDERLINE.match(stripped) and _UNDERLINE.match(following):
            return stripped
    return fallback


def _expand_autoclass(text: str) -> str:
    out: list[str] = []
    skipping_options = False
    for line in text.splitlines():
        if skipping_options and _DIRECTIVE_OPTION.match(line):
            continue
        skipping_options = False
        match = _AUTOCLASS.match(line)
        doc = _class_doc(match[1]) if match else None
        if doc is None:
            out.append(line)
            continue
        out.append(doc)
        skipping_options = True
    return "\n".join(out)


def _class_doc(dotted: str) -> str | None:
    module_name, _, attribute = dotted.rpartition(".")
    try:
        target = getattr(importlib.import_module(module_name), attribute)
    except Exception:
        return None
    return inspect.getdoc(target)
