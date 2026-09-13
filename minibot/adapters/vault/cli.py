from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from getpass import getpass
from pathlib import Path

from minibot.adapters.vault import secrets_yaml
from minibot.adapters.vault.vault import PASSWORD_ENV_VAR, read_vault, write_vault

_DEFAULT_PATH = "secrets.vault.yml"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minibot vault", description="Manage the encrypted credential vault.")
    commands = parser.add_subparsers(dest="action", required=True)
    for action, help_text in (
        ("init", "Create a new empty vault."),
        ("edit", "Decrypt into $EDITOR and re-encrypt on exit."),
        ("list", "Print secret names (never values)."),
    ):
        sub = commands.add_parser(action, help=help_text)
        sub.add_argument("path", nargs="?", default=_DEFAULT_PATH, help=f"Vault file (default: {_DEFAULT_PATH}).")
        sub.add_argument("--password-file", default=None, help="Read the vault password from this file.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(sys.argv[1:] if argv is None else argv)
    path = Path(args.path).expanduser()
    try:
        if args.action == "init":
            _init(path, args.password_file)
        elif args.action == "edit":
            _edit(path, args.password_file)
        else:
            _list(path, args.password_file)
    except (ValueError, OSError) as exc:
        raise SystemExit(f"minibot vault: {exc}") from exc


def _init(path: Path, password_file: str | None) -> None:
    if path.exists():
        raise ValueError(f"{path} already exists; use `minibot vault edit` instead")
    password = _resolve_password(password_file, confirm=True)
    write_vault(path, password, {})
    sys.stdout.write(f"created {path}\n")


def _edit(path: Path, password_file: str | None) -> None:
    password = _resolve_password(password_file)
    secrets = read_vault(path, password)
    edited = _edit_in_editor(secrets_yaml.dumps(secrets))
    # Parse before encrypting: a syntax error must leave the existing vault untouched.
    updated = secrets_yaml.loads(edited)
    if updated == secrets:
        sys.stdout.write("no changes\n")
        return
    write_vault(path, password, updated)
    sys.stdout.write(f"updated {path} ({len(updated)} secrets)\n")


def _list(path: Path, password_file: str | None) -> None:
    secrets = read_vault(path, _resolve_password(password_file))
    sys.stdout.write("".join(f"{name}\n" for name in sorted(secrets)))


def _edit_in_editor(content: str) -> str:
    handle, temp_path = tempfile.mkstemp(prefix="minibot-vault-", suffix=".yml")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        completed = subprocess.run([*editor.split(), temp_path], check=False)
        if completed.returncode != 0:
            raise ValueError(f"{editor} exited with status {completed.returncode}; vault unchanged")
        return Path(temp_path).read_text(encoding="utf-8")
    finally:
        _shred(Path(temp_path))


def _shred(path: Path) -> None:
    # ponytail: best-effort. On a journaling or copy-on-write filesystem the old blocks can survive
    # an overwrite; the real fix is not writing plaintext to disk at all.
    try:
        size = path.stat().st_size
        with path.open("r+b") as stream:
            stream.write(b"\0" * size)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)


def _resolve_password(password_file: str | None, *, confirm: bool = False) -> str:
    if password_file:
        return Path(password_file).expanduser().read_text(encoding="utf-8").strip()
    from_env = os.environ.get(PASSWORD_ENV_VAR)
    if from_env:
        return from_env
    if not sys.stdin.isatty():
        raise ValueError(f"no terminal to prompt on; set {PASSWORD_ENV_VAR} or pass --password-file")
    password = getpass("Vault password: ")
    if not password:
        raise ValueError("vault password must not be empty")
    if confirm and password != getpass("Confirm vault password: "):
        raise ValueError("passwords do not match")
    return password
