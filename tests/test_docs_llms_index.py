from __future__ import annotations

import re
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parents[1] / "docs"
PAGE_URL = re.compile(r"https://sonic182\.github\.io/minibot/(?P<page>[a-z0-9_/-]+)\.html")


def _toctree_entries() -> list[str]:
    entries: list[str] = []
    in_toctree = False
    for line in (DOCS_DIR / "index.rst").read_text().splitlines():
        if line.strip() == ".. toctree::":
            in_toctree = True
            continue
        if not in_toctree:
            continue
        if not line.strip():
            continue
        if line.startswith("   "):
            if not line.strip().startswith(":"):
                entries.append(line.strip())
            continue
        in_toctree = False
    return entries


def _page_stems() -> set[str]:
    stems = {path.stem for path in DOCS_DIR.glob("*.rst")}
    stems |= {path.stem for path in DOCS_DIR.glob("*.md")}
    stems.discard("index")
    return stems


def test_llms_index_only_links_existing_pages() -> None:
    """``llms.txt`` is hand-written, so a renamed page would silently 404 from it."""
    pages = PAGE_URL.findall((DOCS_DIR / "llms.txt").read_text())
    assert pages, "llms.txt links no documentation pages"
    missing = [page for page in pages if not _page_source_exists(page)]
    assert not missing, f"llms.txt links pages with no source file: {missing}"


def test_toctree_entries_have_source_files() -> None:
    missing = [entry for entry in _toctree_entries() if not _page_source_exists(entry)]
    assert not missing, f"index.rst toctree references missing pages: {missing}"


def test_every_page_is_in_a_toctree() -> None:
    orphaned = sorted(_page_stems() - set(_toctree_entries()))
    assert not orphaned, f"docs pages missing from any toctree: {orphaned}"


def _page_source_exists(page: str) -> bool:
    return any((DOCS_DIR / f"{page}{suffix}").exists() for suffix in (".rst", ".md"))
