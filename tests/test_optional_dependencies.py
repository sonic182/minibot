"""Guards for the optional-dependency contract.

Two failure modes this catches, both invisible under ``poetry install --all-extras``:

- a dependency marked ``optional = true`` that no extra references, so ``pip`` can never install it;
- a bundled extension importing a third-party package at module level, which makes that package
  mandatory for every entrypoint regardless of the config gate.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

OPTIONAL_THIRD_PARTY = (
    "aiogram",
    "telegramify_markdown",
    "aio_pika",
    "pypdf",
    "faster_whisper",
    "sentence_transformers",
    "mcp",
)

_IMPORT_WITH_OPTIONALS_BLOCKED = """
import importlib
import sys

BLOCKED = {blocked!r}


class _BlockOptional:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"blocked optional dependency: {{fullname}}")
        return None


sys.meta_path.insert(0, _BlockOptional())

from minibot.app.extensions import _bundled_modules

for entrypoint in ("daemon", "console", "worker"):
    for name in _bundled_modules(entrypoint):
        importlib.import_module(name)
"""


def test_every_optional_dependency_belongs_to_an_extra() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    declared = pyproject["tool"]["poetry"]["dependencies"]
    optional = {name for name, spec in declared.items() if isinstance(spec, dict) and spec.get("optional")}
    in_an_extra = {name for names in pyproject["tool"]["poetry"]["extras"].values() for name in names}
    assert optional <= in_an_extra, f"optional dependencies no extra can install: {sorted(optional - in_an_extra)}"


def test_bundled_extensions_import_without_optional_dependencies() -> None:
    script = _IMPORT_WITH_OPTIONALS_BLOCKED.format(blocked=OPTIONAL_THIRD_PARTY)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=PROJECT_ROOT)
    assert result.returncode == 0, f"bundled extensions require an optional dependency:\n{result.stderr}"
