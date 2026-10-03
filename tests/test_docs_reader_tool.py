from __future__ import annotations

from pathlib import Path

import pytest

from minibot.core.tools import ToolContext
from minibot.llm.tools.docs_reader import DocsReaderTool, docs_root


async def _call(tool: DocsReaderTool, **payload: object) -> dict[str, object]:
    [binding] = tool.bindings()
    return await binding.handler({"page": None, "query": None, **payload}, ToolContext())


@pytest.mark.asyncio
async def test_lists_pages_and_expands_config_reference() -> None:
    tool = DocsReaderTool()

    listing = await _call(tool)
    page = await _call(tool, page="config")

    assert "config" in {entry["page"] for entry in listing["pages"]}  # type: ignore[union-attr,index]
    assert page["ok"] is True
    assert ".. autoclass::" not in page["content"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_query_finds_a_known_key() -> None:
    result = await _call(DocsReaderTool(), query="native_disabled")

    assert result["ok"] is True
    assert "skills" in {hit["page"] for hit in result["results"]}  # type: ignore[union-attr,index]


@pytest.mark.asyncio
async def test_unknown_page_is_a_structured_error() -> None:
    result = await _call(DocsReaderTool(), page="../pyproject")

    assert result["ok"] is False
    assert result["error_code"] == "page_not_found"


@pytest.mark.asyncio
async def test_empty_root_reports_docs_unavailable(tmp_path: Path) -> None:
    result = await _call(DocsReaderTool(root=tmp_path / "missing"), page="config")

    assert result["ok"] is False
    assert result["error_code"] == "docs_unavailable"


def test_docs_root_resolves_in_the_repo() -> None:
    assert docs_root() is not None
