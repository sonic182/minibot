import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())

project = 'minibot'
copyright = '2026, sonic182'
author = 'sonic182'
release = _pyproject["tool"]["poetry"]["version"]

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.githubpages',
]

autodoc_member_order = 'bysource'
autodoc_mock_imports = ['faster_whisper', 'mcp']

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

language = 'en'

# html_theme = 'alabaster'
html_theme = "shibuya"
html_static_path = ['_static']
