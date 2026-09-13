from __future__ import annotations

from pathlib import Path

import pytest

from minibot.adapters.config.environment import has_secret_references, has_secret_syntax
from minibot.adapters.config.schema import Settings, VaultConfig
from minibot.adapters.vault import Vault, write_vault


def _unlocked_vault(tmp_path: Path, secrets: dict[str, str]) -> Vault:
    path = tmp_path / "secrets.vault.yml"
    write_vault(path, "pw", secrets)
    vault = Vault(VaultConfig(enabled=True, path=str(path)))
    vault.unlock("pw")
    return vault


def test_secret_references_resolve_alongside_env_references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIBOT_TEST_BASE_URL", "https://api.example.com")
    vault = _unlocked_vault(tmp_path, {"openrouter": "sk-real-key", "telegram": "123:abc"})
    data = {
        "providers": {"openrouter": {"api_key": "${secret:openrouter}", "base_url": "${MINIBOT_TEST_BASE_URL}"}},
        "channels": {"telegram": {"bot_token": "${secret:telegram}"}},
        "orchestration": {"directory": "$${secret:openrouter}"},
    }

    settings = Settings.from_dict(data, vault.as_mapping())

    assert settings.providers["openrouter"].api_key == "sk-real-key"
    assert settings.providers["openrouter"].base_url == "https://api.example.com"
    assert settings.channels.telegram.bot_token == "123:abc"
    # `$$` escapes a reference, exactly as it does for ${ENV_VAR}.
    assert settings.orchestration.directory == "${secret:openrouter}"


def test_unresolvable_secret_references_fail_loudly(tmp_path: Path) -> None:
    vault = _unlocked_vault(tmp_path, {"known": "value"})
    data = {"providers": {"openrouter": {"api_key": "${secret:missing}"}}}

    with pytest.raises(ValueError, match=r"secret missing is not in the vault for providers.openrouter.api_key"):
        Settings.from_dict(data, vault.as_mapping())

    with pytest.raises(ValueError, match=r"\[vault\] settings cannot use"):
        Settings.from_dict({"vault": {"path": "${secret:known}"}}, vault.as_mapping())


def test_references_are_left_literal_without_a_vault() -> None:
    """The wizard round-trips config to disk, so it must preserve references, not resolve them."""
    data = {"providers": {"openrouter": {"api_key": "${secret:openrouter}"}}}

    settings = Settings.from_dict(data)

    assert settings.providers["openrouter"].api_key == "${secret:openrouter}"
    assert has_secret_references(settings.model_dump(mode="python"))


def test_a_config_without_references_never_needs_the_vault() -> None:
    data = {"providers": {"openrouter": {"api_key": "plain"}}, "orchestration": {"directory": "$${secret:x}"}}

    assert not has_secret_references(Settings.from_dict(data).model_dump(mode="python"))


def test_an_escape_is_consumed_even_with_no_vault_and_nothing_to_resolve() -> None:
    """`$$` is dropped by the secret pass, so a config of escapes alone still has to run it."""
    data = {"orchestration": {"directory": "$${secret:x}"}}

    assert has_secret_syntax(Settings.from_dict(data).model_dump(mode="python"))
    assert Settings.from_dict(data, {}).orchestration.directory == "${secret:x}"
