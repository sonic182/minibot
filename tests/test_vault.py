from __future__ import annotations

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
