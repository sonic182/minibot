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

[channels.telegram]
bot_token = "token"
"""
    )

    settings = load_settings(config_file)
    assert settings.runtime.log_level == "DEBUG"
    assert settings.llm.api_key == "secret"
    assert settings.channels["telegram"].bot_token == "token"
