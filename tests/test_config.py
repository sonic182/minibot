from pathlib import Path

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
