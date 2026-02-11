from pathlib import Path

import pytest

from minibot.adapters.config.loader import load_settings


def test_load_settings_from_file(tmp_path: Path) -> None:
    config_file = tmp_path / "bot.toml"
    config_file.write_text(
        """
[runtime]
log_level = "DEBUG"
agent_timeout_seconds = 180

[llm]
provider = "openai"
api_key = "secret"
request_timeout_seconds = 50
sock_connect_timeout_seconds = 12
sock_read_timeout_seconds = 50
retry_attempts = 4
retry_delay_seconds = 2.0

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
    assert settings.runtime.agent_timeout_seconds == 180
    assert settings.llm.api_key == "secret"
    assert settings.llm.request_timeout_seconds == 50
    assert settings.llm.sock_connect_timeout_seconds == 12
    assert settings.llm.sock_read_timeout_seconds == 50
    assert settings.llm.retry_attempts == 4
    assert settings.llm.retry_delay_seconds == 2.0
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
artifacts_max_file_bytes = "5MB"
artifacts_max_total_bytes = "20MB"

[tools.file_storage]
enabled = true
root_dir = "./data/files"
max_write_bytes = "80KB"
save_incoming_uploads = true
uploads_subdir = "uploads"
incoming_temp_subdir = "uploads/temp"
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
    assert settings.tools.python_exec.artifacts_max_file_bytes == 5_000_000
    assert settings.tools.python_exec.artifacts_max_total_bytes == 20_000_000
    assert settings.tools.file_storage.enabled is True
    assert settings.tools.file_storage.root_dir == "./data/files"
    assert settings.tools.file_storage.max_write_bytes == 80_000
    assert settings.tools.file_storage.save_incoming_uploads is True
    assert settings.tools.file_storage.uploads_subdir == "uploads"
    assert settings.tools.file_storage.incoming_temp_subdir == "uploads/temp"


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
