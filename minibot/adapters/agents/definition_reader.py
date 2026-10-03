from __future__ import annotations

from pathlib import Path


class LocalAgentDefinitionReader:
    """Read Markdown definition files from one local directory."""

    def read(self, directory: str) -> list[tuple[Path, str]]:
        root = Path(directory)
        if not root.is_dir():
            return []
        return [(path, path.read_text(encoding="utf-8")) for path in sorted(root.glob("*.md"))]
