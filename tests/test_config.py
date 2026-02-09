from pathlib import Path

import pytest

from minibot.adapters.config.loader import load_settings


def test_load_settings_from_file(tmp_path: Path) -> None:
    config_file = tmp_path / "bot.toml"
    config_file.write_text(
        """
[runtime]
log_level = "DEBUG"

[llm]
provider = "openai"
api_key = "secret"

[llm.openrouter]
models = ["anthropic/claude-3.5-sonnet", "gryphe/mythomax-l2-13b"]
plugins = [{ id = "file-parser", pdf = { engine = "pdf-text" } }]

[llm.openrouter.provider]
order = ["anthropic", "openai"]
allow_fallbacks = true
data_collection = "deny"
provider_extra = { custom_hint = "value" }

[channels.telegram]
bot_token = "token"
"""
    )

    settings = load_settings(config_file)
    assert settings.runtime.log_level == "DEBUG"
    assert settings.llm.api_key == "secret"
    assert settings.llm.openrouter.models == ["anthropic/claude-3.5-sonnet", "gryphe/mythomax-l2-13b"]
    assert settings.llm.openrouter.plugins == [{"id": "file-parser", "pdf": {"engine": "pdf-text"}}]
    assert settings.llm.openrouter.provider is not None
    assert settings.llm.openrouter.provider.order == ["anthropic", "openai"]
    assert settings.llm.openrouter.provider.provider_extra["custom_hint"] == "value"
    assert settings.channels["telegram"].bot_token == "token"


def test_load_settings_accepts_human_readable_byte_sizes(tmp_path: Path) -> None:
    config_file = tmp_path / "bot.toml"
    config_file.write_text(
        """
[llm]
provider = "openai"
api_key = "secret"

[channels.telegram]
bot_token = "token"
max_photo_bytes = "5MB"
max_document_bytes = "10MB"
max_total_media_bytes = "12MB"

[tools.http_client]
enabled = true
max_bytes = "16KB"

[tools.playwright]
enabled = true
max_screenshot_bytes = "2MB"

[tools.python_exec]
max_output_bytes = "64KB"
max_code_bytes = "32KB"

[tools.file_storage]
max_write_bytes = "64KB"
max_read_bytes = "128KB"
"""
    )

    settings = load_settings(config_file)
    assert settings.channels["telegram"].max_photo_bytes == 5_000_000
    assert settings.channels["telegram"].max_document_bytes == 10_000_000
    assert settings.channels["telegram"].max_total_media_bytes == 12_000_000
    assert settings.tools.http_client.max_bytes == 16_000
    assert settings.tools.playwright.max_screenshot_bytes == 2_000_000
    assert settings.tools.python_exec.max_output_bytes == 64_000
    assert settings.tools.python_exec.max_code_bytes == 32_000
    assert settings.tools.file_storage.max_write_bytes == 64_000
    assert settings.tools.file_storage.max_read_bytes == 128_000


def test_load_settings_rejects_invalid_byte_size(tmp_path: Path) -> None:
    config_file = tmp_path / "bot.toml"
    config_file.write_text(
        """
[llm]
provider = "openai"
api_key = "secret"

[channels.telegram]
bot_token = "token"
max_photo_bytes = "nope"
"""
    )

    with pytest.raises(ValueError):
        load_settings(config_file)
