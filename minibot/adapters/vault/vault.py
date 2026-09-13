from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from getpass import getpass
from pathlib import Path
from types import MappingProxyType

from minibot.adapters.config.schema import VaultConfig
from minibot.adapters.vault import crypt, secrets_yaml

PASSWORD_ENV_VAR = "MINIBOT_VAULT_PASSWORD"


def read_vault(path: str | Path, password: str) -> dict[str, str]:
    raw = Path(path).expanduser().read_text(encoding="utf-8")
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not a minibot vault file: {exc}") from exc
    plaintext = crypt.decrypt(envelope, password)
    return secrets_yaml.loads(plaintext.decode("utf-8"))


def write_vault(path: str | Path, password: str, secrets: dict[str, str]) -> None:
    envelope = crypt.encrypt(secrets_yaml.dumps(secrets).encode("utf-8"), password)
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    target.chmod(0o600)


class Vault:
    """Decrypted secrets held in process memory only.

    The key is derived at unlock time and never written anywhere: not to disk, not to a subprocess
    environment. ``get`` is reachable from trusted adapter code; the LLM only ever sees ``names``.
    """

    def __init__(self, config: VaultConfig) -> None:
        self._config = config
        self._secrets: dict[str, str] | None = None

    def unlock(self, password: str) -> None:
        self._secrets = read_vault(self._config.path, password)

    def names(self) -> list[str]:
        return sorted(self._require())

    def as_mapping(self) -> Mapping[str, str]:
        """Read-only view for ``${secret:NAME}`` expansion. Never hand this to LLM-facing code."""
        return MappingProxyType(self._require())

    def get(self, name: str) -> str:
        secrets = self._require()
        if name not in secrets:
            raise ValueError(f"vault has no secret named {name!r}")
        return secrets[name]

    def _require(self) -> dict[str, str]:
        if self._secrets is None:
            raise ValueError("vault is locked")
        return self._secrets


def read_vault_password(config: VaultConfig, logger: logging.Logger) -> str:
    """Resolve the unlock password: password file, then environment, then an interactive prompt.

    The interactive prompt is the recommended method — it is the only one where no password
    material touches disk or the process environment.
    """
    if config.password_file:
        logger.warning(
            "vault unlocked from a password file; any tool able to read the filesystem can read it too",
            extra={"password_file": config.password_file},
        )
        return Path(config.password_file).expanduser().read_text(encoding="utf-8").strip()
    from_env = os.environ.get(PASSWORD_ENV_VAR)
    if from_env:
        logger.warning(f"vault unlocked from ${PASSWORD_ENV_VAR}; protect that variable as you would the vault itself")
        return from_env
    if not sys.stdin.isatty():
        raise ValueError(
            "vault is enabled but there is no terminal to prompt on. Set [vault] password_file, "
            f"export {PASSWORD_ENV_VAR}, or start minibot from a terminal."
        )
    return getpass("Vault password: ")
