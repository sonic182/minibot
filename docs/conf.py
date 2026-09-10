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

# Machine-readable LLM index, copied to the built site root (llms.txt / llms-full.txt).
html_extra_path = ["llms.txt", "llms-full.txt"]

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.githubpages",
    "sphinxcontrib.mermaid",
]

# Mermaid renders client-side in the browser; no node/mermaid-cli needed for HTML builds.
mermaid_output_format = "raw"

autodoc_member_order = "bysource"
autodoc_mock_imports = ["faster_whisper", "mcp"]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

language = "en"

# html_theme = 'alabaster'
html_theme = "shibuya"
html_static_path = ["_static"]
