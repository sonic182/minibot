from __future__ import annotations

from pathlib import Path

from minibot.adapters.config.schema import Settings


def build_environment_prompt_fragment(settings: Settings) -> str:
    lines: list[str] = []
    lines.append(f"- Process working directory (cwd): {Path('.').resolve().as_posix()}")
    file_storage = getattr(settings.tools, "file_storage", None)
    if getattr(file_storage, "enabled", False):
        root_dir = getattr(file_storage, "root_dir", "")
        if isinstance(root_dir, str) and root_dir.strip():
            resolved_root = Path(root_dir).expanduser().resolve().as_posix()
            mode = "yolo" if bool(getattr(file_storage, "allow_outside_root", False)) else "confined"
            lines.append(f"- Filesystem managed root (configured): {root_dir}")
            lines.append(f"- Filesystem managed root (resolved): {resolved_root}")
            lines.append(f"- Filesystem mode: {mode}")
            lines.append("- Path rule: inside root use relative paths; outside root (yolo mode) use absolute paths.")
    browser = getattr(settings.tools, "browser", None)
    browser_output_dir = getattr(browser, "output_dir", "")
    if not isinstance(browser_output_dir, str):
        browser_output_dir = ""
    browser_output_dir = browser_output_dir.strip()
    if browser_output_dir:
        lines.append(f"- Browser artifacts directory: {browser_output_dir}")
        lines.append(
            "- For browser screenshots/downloads, save in the browser artifacts directory and return the saved path."
        )
    if not lines:
        return ""
    return "Environment context:\n" + "\n".join(lines)
