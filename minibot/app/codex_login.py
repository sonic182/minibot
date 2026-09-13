from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from minibot.adapters.config.loader import load_settings
from minibot.llm.services.client_bootstrap import resolve_codex_auth_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minibot codex login")
    parser.add_argument(
        "--device-code",
        action="store_true",
        help="Use the device-code flow instead of opening a local browser (needed on headless/Docker hosts).",
    )
    parser.add_argument(
        "--auth-file",
        type=str,
        default=None,
        help="Override the credentials path (default: [providers.chatgpt_codex].auth_path, or "
        "~/.minibot/auth_codex.json).",
    )
    parser.add_argument("--config", type=str, default=None, help="Optional config.toml path.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed login progress.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)

    try:
        from llm_async_codex import CodexLoginError, login
    except ImportError as exc:
        raise SystemExit(
            'llm-async-codex is required for "minibot codex login". '
            "Install with `poetry install --extras codex` or `poetry install --all-extras`."
        ) from exc

    settings = load_settings(Path(args.config) if args.config else None)
    provider_cfg = settings.providers.get("chatgpt_codex")
    configured_auth_path = provider_cfg.auth_path if provider_cfg else None
    auth_path = resolve_codex_auth_path(args.auth_file or configured_auth_path)

    try:
        asyncio.run(login(device_code=args.device_code, auth_path=auth_path, verbose=args.verbose))
    except CodexLoginError as exc:
        raise SystemExit(f"Codex login failed: {exc}") from exc


if __name__ == "__main__":
    main()
