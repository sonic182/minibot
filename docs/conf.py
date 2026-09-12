import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())

project = "minibot"
copyright = "2026, sonic182"
author = "sonic182"
release = _pyproject["tool"]["poetry"]["version"]

# SEO: the site <title> (the index page uses this verbatim; subpages become "<Page> - <this>").
html_title = "Minibot — Self-Hosted AI Assistant for Telegram"
html_short_title = "Minibot"

# Canonical base URL: used by sphinx-sitemap and for absolute links.
html_baseurl = "https://sonic182.github.io/minibot/"

# The site is not built as per-language subdirectories, so drop the default "{lang}" prefix
# from sitemap URLs and keep generated index/search pages out of it.
sitemap_url_scheme = "{link}"
sitemap_excludes = ["genindex.html", "py-modindex.html", "search.html", "_modules/*"]

# Machine-readable LLM index, copied to the built site root (llms.txt / llms-full.txt).
html_extra_path = ["llms.txt", "llms-full.txt"]

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.githubpages",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinxcontrib.mermaid",
    "myst_parser",
    "sphinx_sitemap",
]

# reStructuredText for authored pages, Markdown for mirrored repo docs (ARCHITECTURE.md).
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# Mermaid renders client-side in the browser; no node/mermaid-cli needed for HTML builds.
mermaid_output_format = "raw"

# MyST: colon fences, definition lists, and stable heading anchors in mirrored Markdown.
myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 2
# Render ```mermaid fences in mirrored Markdown through sphinxcontrib-mermaid.
myst_fence_as_directive = ["mermaid"]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_mock_imports = [
    "aio_pika",
    "aiogram",
    "faster_whisper",
    "mcp",
    "networkx",
    "numpy",
    "pypdf",
    "qdrant_client",
    "sentence_transformers",
    "torch",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

language = "en"

# html_theme = 'alabaster'
html_theme = "shibuya"
html_theme_options = {
    "github_url": "https://github.com/sonic182/minibot",
    "show_ai_links": True,
}
html_static_path = ["_static"]
