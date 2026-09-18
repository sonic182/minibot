import sys
import tomllib
from pathlib import Path

from docutils import nodes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())

project = "minibot"
copyright = "2026, sonic182"
author = "sonic182"
release = _pyproject["tool"]["poetry"]["version"]

# SEO: the site <title> (the index page uses this verbatim; subpages become "<Page> - <this>").
html_title = "Minibot AI Assistant"
html_short_title = "Minibot"

# Canonical base URL: used by sphinx-sitemap and for absolute links.
html_baseurl = "https://sonic182.github.io/minibot/"

# The site is not built as per-language subdirectories, so drop the default "{lang}" prefix
# from sitemap URLs and keep generated index/search pages out of it.
sitemap_url_scheme = "{link}"
sitemap_excludes = ["genindex.html", "py-modindex.html", "search.html", "_modules/*"]
# Requires the git history (CI checks out with fetch-depth: 0); emits a warning on a shallow clone.
sitemap_show_lastmod = True

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

# reStructuredText for most pages; Markdown (MyST) for the architecture page.
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# Mermaid renders client-side in the browser; no node/mermaid-cli needed for HTML builds.
mermaid_output_format = "raw"

# MyST: colon fences, definition lists, and stable heading anchors in Markdown pages.
myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 2
# Render ```mermaid fences in Markdown pages through sphinxcontrib-mermaid.
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
# Declaring one stops the browser from requesting /favicon.ico at the host root, which 404s
# on a GitHub Pages project site served under /minibot/.
html_favicon = "_static/favicon.svg"


def _page_meta(app, pagename, templatename, context, doctree):
    # Shibuya builds og:description from the `meta` context dict, but Sphinx only exposes
    # `.. meta::` through the rendered `metatags` string; without this bridge there is none.
    meta = dict(context.get("meta") or {})
    if doctree is not None:
        for node in doctree.findall(nodes.meta):
            name = node.get("name")
            if name in {"description", "keywords"}:
                meta.setdefault(name, node["content"])
    context["meta"] = meta
    if pagename == app.config.root_doc:
        # Subpages keep the short `html_title` suffix; only the home page gets the long
        # title, matching its H1 and og:title.
        context["docstitle"] = "Minibot — Self-Hosted AI Assistant for Telegram"


def setup(app):
    app.connect("html-page-context", _page_meta)
