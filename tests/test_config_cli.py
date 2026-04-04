from __future__ import annotations

from pathlib import Path

import pytest

from minibot.adapters.config.loader import load_settings
from minibot.adapters.config.lua_serializer import settings_to_lua_text
from minibot.adapters.config.schema import ProviderConfig, Settings
from minibot.app import config_cli


def test_settings_to_lua_text_serializes_supported_values() -> None:
    settings = Settings()
    settings.providers = {
        "openai-alt": ProviderConfig(
            api_key="line1\nline2",
            base_url="https://example.test/v1",
        )
    }
    settings.channels["telegram"].allowed_chat_ids = []
    settings.channels["telegram"].allowed_user_ids = [1, 2]
    settings.tools.http_client.enabled = True
    settings.tools.http_client.max_chars = 123
    settings.tools.http_client.spill_to_managed_file = True
    settings.tools.http_client.spill_after_chars = 456
    settings.tools.http_client.spill_preview_chars = 78
    settings.tools.http_client.spill_subdir = "http/tmp"

    lua_text = settings_to_lua_text(Settings.model_validate(settings.model_dump(mode="python")))

    assert lua_text.startswith("return {\n")
    assert '["openai-alt"]' in lua_text
    assert 'api_key = "line1\\nline2"' in lua_text
    assert "allowed_chat_ids = {}," in lua_text
    assert "allowed_user_ids = {\n" in lua_text
    assert "enabled = true," in lua_text
    assert "max_chars = 123," in lua_text
    assert "spill_to_managed_file = true," in lua_text
    assert "spill_after_chars = 456," in lua_text
    assert 'spill_subdir = "http/tmp"' in lua_text


def test_config_cli_converts_toml_to_lua(tmp_path: Path) -> None:
    input_path = tmp_path / "config.toml"
    output_path = tmp_path / "config.lua"
    input_path.write_text(
        """
[llm]
provider = "openai"
api_key = "secret"

[channels.telegram]
bot_token = "token"
allowed_chat_ids = [123]

[tools.http_client]
enabled = true
max_chars = 321
spill_to_managed_file = true
""",
        encoding="utf-8",
    )

    exit_code = config_cli.main(["toml-to-lua", str(input_path), "--output", str(output_path)])

    assert exit_code == 0
    assert output_path.exists()
    generated = output_path.read_text(encoding="utf-8")
    assert generated.startswith("return {\n")
    assert 'api_key = "secret"' in generated
    assert "allowed_chat_ids = {\n" in generated
    assert "123," in generated
    assert "spill_to_managed_file = true," in generated


def test_config_cli_requires_output_argument() -> None:
    with pytest.raises(SystemExit) as exc_info:
        config_cli.main(["toml-to-lua", "config.toml"])

    assert exc_info.value.code == 2


def test_config_cli_rejects_invalid_config_without_writing_output(tmp_path: Path) -> None:
    input_path = tmp_path / "config.toml"
    output_path = tmp_path / "config.lua"
    input_path.write_text(
        """
[llm]
provider = "openai"
api_key = "secret"

[channels.telegram]
bot_token = "token"
max_photo_bytes = "nope"
""",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="invalid byte size value"):
        config_cli.main(["toml-to-lua", str(input_path), "--output", str(output_path)])

    assert not output_path.exists()


def test_config_cli_rejects_non_toml_input(tmp_path: Path) -> None:
    input_path = tmp_path / "config.lua"
    output_path = tmp_path / "generated.lua"
    input_path.write_text("return {}", encoding="utf-8")

    with pytest.raises(SystemExit, match="input config must use the \\.toml extension"):
        config_cli.main(["toml-to-lua", str(input_path), "--output", str(output_path)])

    assert not output_path.exists()


def test_generated_lua_round_trips_through_loader(tmp_path: Path) -> None:
    pytest.importorskip("lupa")

    input_path = tmp_path / "config.toml"
    output_path = tmp_path / "config.lua"
    input_path.write_text(
        """
[runtime]
log_level = "DEBUG"

[llm]
provider = "openai"
api_key = "secret"

[channels.telegram]
bot_token = "token"
allowed_chat_ids = []
allowed_user_ids = [5]

[providers.openai]
api_key = "provider-secret"
base_url = "https://example.test/v1"

[tools.mcp]
enabled = true

[[tools.mcp.servers]]
name = "playwright"
transport = "http"
url = "http://127.0.0.1:3000/mcp"
headers = { Authorization = "Bearer test" }
""",
        encoding="utf-8",
    )

    config_cli.main(["toml-to-lua", str(input_path), "--output", str(output_path)])

    from_toml = load_settings(input_path)
    from_lua = load_settings(output_path)

    assert from_lua.model_dump(mode="python") == from_toml.model_dump(mode="python")
