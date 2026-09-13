from __future__ import annotations

import json
from pathlib import Path

import pytest

from minibot.adapters.vault import secrets_yaml
from minibot.adapters.vault.vault import read_vault, write_vault

_AWKWARD_VALUES = {
    "numeric": "12345",
    "boolean_ish": "true",
    "colon_inside": "Bearer a: b",
    "pem": "-----BEGIN KEY-----\nabc\n-----END KEY-----",
    "padded": "  spaced  ",
    "empty": "",
    "hash_start": "#notacomment",
}


def test_vault_round_trips_through_the_encrypted_file(tmp_path: Path) -> None:
    path = tmp_path / "secrets.vault.yml"
    secrets = {"github": "ghp_example", **_AWKWARD_VALUES}

    write_vault(path, "correct horse", secrets)

    assert read_vault(path, "correct horse") == secrets
    assert "ghp_example" not in path.read_text(encoding="utf-8")
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="incorrect or the vault file is corrupt"):
        read_vault(path, "wrong password")


def test_a_truncated_vault_file_fails_as_a_value_error(tmp_path: Path) -> None:
    """`minibot vault` only catches ValueError/OSError, so a KeyError here is a raw traceback."""
    path = tmp_path / "secrets.vault.yml"
    write_vault(path, "pw", {"github": "ghp_example"})
    envelope = json.loads(path.read_text(encoding="utf-8"))
    del envelope["n"]
    path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(ValueError, match="malformed vault envelope"):
        read_vault(path, "pw")


def test_a_missing_vault_file_fails_as_a_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read the vault at"):
        read_vault(tmp_path / "nope.vault.yml", "pw")


def test_secrets_document_never_coerces_value_types() -> None:
    parsed = secrets_yaml.loads(secrets_yaml.dumps(_AWKWARD_VALUES))

    assert parsed == _AWKWARD_VALUES
    assert all(isinstance(value, str) for value in parsed.values())


def test_secrets_document_rejects_malformed_input() -> None:
    assert secrets_yaml.loads("# a comment\n\ngithub: token\n") == {"github": "token"}
    with pytest.raises(ValueError, match="duplicate secret name"):
        secrets_yaml.loads("github: one\ngithub: two\n")
    with pytest.raises(ValueError, match="expected `name: value`"):
        secrets_yaml.loads("github\n")
