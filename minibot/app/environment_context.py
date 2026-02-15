from __future__ import annotations

from minibot.adapters.config.schema import Settings


def build_environment_prompt_fragment(settings: Settings) -> str:
    lines: list[str] = []
    file_storage = getattr(settings.tools, "file_storage", None)
    if getattr(file_storage, "enabled", False):
        root_dir = getattr(file_storage, "root_dir", "")
        if isinstance(root_dir, str) and root_dir.strip():
            lines.append(f"- Managed file workspace root: {root_dir}")
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
