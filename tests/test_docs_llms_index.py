from __future__ import annotations

import re
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
PAGE_URL = re.compile(r"https://sonic182\.github\.io/minibot/(?P<page>[a-z0-9_/-]+)\.html")


def test_llms_index_only_links_existing_pages() -> None:
    """``llms.txt`` is hand-written, so a renamed page would silently 404 from it."""
    pages = PAGE_URL.findall((DOCS_DIR / "llms.txt").read_text())
    assert pages, "llms.txt links no documentation pages"
    missing = [page for page in pages if not (DOCS_DIR / f"{page}.rst").exists()]
    assert not missing, f"llms.txt links pages with no source file: {missing}"
