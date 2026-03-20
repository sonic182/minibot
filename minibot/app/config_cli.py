from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from minibot.adapters.config.lua_serializer import convert_toml_to_lua_file


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minibot-config")
    subparsers = parser.add_subparsers(dest="command", required=True)

    toml_to_lua_parser = subparsers.add_parser("toml-to-lua", help="Convert a MiniBot TOML config into Lua.")
    toml_to_lua_parser.add_argument("input", type=str, help="Path to the source config.toml file.")
    toml_to_lua_parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Path to the destination config.lua file.",
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.command != "toml-to-lua":
        raise ValueError(f"unsupported command: {args.command}")
    try:
        convert_toml_to_lua_file(Path(args.input).expanduser(), Path(args.output).expanduser())
    except (OSError, ValidationError, ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
