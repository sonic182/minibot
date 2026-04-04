from pathlib import Path

import pytest

from minibot.adapters.config.loader import load_settings
from minibot.adapters.config.schema import Settings
from minibot.adapters.lua import runtime as lua_runtime


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
http2 = true
request_timeout_seconds = 50
sock_connect_timeout_seconds = 12
sock_read_timeout_seconds = 50
retry_attempts = 4
retry_delay_seconds = 2.0
main_responses_state_mode = "full_messages"
agent_responses_state_mode = "previous_response_id"
prompt_cache_enabled = false
prompt_cache_retention = "24h"

[llm.openrouter]
models = ["anthropic/claude-3.5-sonnet", "gryphe/mythomax-l2-13b"]
plugins = [{ id = "file-parser", pdf = { engine = "pdf-text" } }]

[llm.openrouter.provider]
order = ["anthropic", "openai"]
allow_fallbacks = true
data_collection = "deny"
provider_extra = { custom_hint = "value" }

[llm.xai]
web_search_enabled = true
x_search_enabled = true

[llm.xai.web_search]
allowed_domains = ["x.ai"]
excluded_domains = ["example.com"]
enable_image_understanding = true

[llm.xai.x_search]
allowed_x_handles = ["xai"]
excluded_x_handles = ["spam_account"]
from_date = "2026-03-01T00:00:00+00:00"
to_date = "2026-03-10T00:00:00+00:00"
enable_image_understanding = true
enable_video_understanding = true

[channels.telegram]
bot_token = "token"

[memory]
context_ratio_before_compact = 0.9
"""
    )

    settings = load_settings(config_file)
    assert settings.runtime.log_level == "DEBUG"
    assert settings.runtime.agent_timeout_seconds == 180
    assert settings.llm.api_key == "secret"
    assert settings.llm.http2 is True
    assert settings.llm.request_timeout_seconds == 50
    assert settings.llm.sock_connect_timeout_seconds == 12
    assert settings.llm.sock_read_timeout_seconds == 50
    assert settings.llm.retry_attempts == 4
    assert settings.llm.retry_delay_seconds == 2.0
    assert settings.llm.main_responses_state_mode == "full_messages"
    assert settings.llm.agent_responses_state_mode == "previous_response_id"
    assert settings.llm.prompt_cache_enabled is False
    assert settings.llm.prompt_cache_retention == "24h"
    assert settings.llm.openrouter.models == ["anthropic/claude-3.5-sonnet", "gryphe/mythomax-l2-13b"]
    assert settings.llm.openrouter.plugins == [{"id": "file-parser", "pdf": {"engine": "pdf-text"}}]
    assert settings.llm.openrouter.provider is not None
    assert settings.llm.openrouter.provider.order == ["anthropic", "openai"]
    assert settings.llm.openrouter.provider.provider_extra["custom_hint"] == "value"
    assert settings.llm.xai.web_search_enabled is True
    assert settings.llm.xai.x_search_enabled is True
    assert settings.llm.xai.web_search.allowed_domains == ["x.ai"]
    assert settings.llm.xai.web_search.excluded_domains == ["example.com"]
    assert settings.llm.xai.web_search.enable_image_understanding is True
    assert settings.llm.xai.x_search.allowed_x_handles == ["xai"]
    assert settings.llm.xai.x_search.excluded_x_handles == ["spam_account"]
    assert settings.llm.xai.x_search.from_date == "2026-03-01T00:00:00+00:00"
    assert settings.llm.xai.x_search.to_date == "2026-03-10T00:00:00+00:00"
    assert settings.llm.xai.x_search.enable_image_understanding is True
    assert settings.llm.xai.x_search.enable_video_understanding is True
    assert settings.channels["telegram"].bot_token == "token"
    assert settings.memory.context_ratio_before_compact == 0.9
    assert settings.tools.browser.output_dir == "./data/files/browser"


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

[tools.python_exec]
max_output_bytes = "64KB"
max_code_bytes = "32KB"
artifacts_max_file_bytes = "5MB"
artifacts_max_total_bytes = "20MB"

[tools.bash]
enabled = true
max_output_bytes = "128KB"

[tools.apply_patch]
enabled = true
max_patch_bytes = "256KB"

[tools.file_storage]
enabled = true
root_dir = "./data/files"
max_write_bytes = "80KB"
allow_outside_root = true
save_incoming_uploads = true
uploads_subdir = "uploads"
incoming_temp_subdir = "uploads/temp"

[tools.grep]
enabled = true
max_matches = 123
max_file_size_bytes = "2MB"
"""
    )

    settings = load_settings(config_file)
    assert settings.channels["telegram"].max_photo_bytes == 5_000_000
    assert settings.channels["telegram"].max_document_bytes == 10_000_000
    assert settings.channels["telegram"].max_total_media_bytes == 12_000_000
    assert settings.tools.http_client.max_bytes == 16_000
    assert settings.tools.python_exec.max_output_bytes == 64_000
    assert settings.tools.python_exec.max_code_bytes == 32_000
    assert settings.tools.python_exec.artifacts_max_file_bytes == 5_000_000
    assert settings.tools.python_exec.artifacts_max_total_bytes == 20_000_000
    assert settings.tools.bash.enabled is True
    assert settings.tools.bash.max_output_bytes == 128_000
    assert settings.tools.apply_patch.enabled is True
    assert settings.tools.apply_patch.max_patch_bytes == 256_000
    assert settings.tools.file_storage.enabled is True
    assert settings.tools.file_storage.root_dir == "./data/files"
    assert settings.tools.file_storage.max_write_bytes == 80_000
    assert settings.tools.file_storage.allow_outside_root is True
    assert settings.tools.file_storage.save_incoming_uploads is True
    assert settings.tools.file_storage.uploads_subdir == "uploads"
    assert settings.tools.file_storage.incoming_temp_subdir == "uploads/temp"
    assert settings.tools.grep.enabled is True
    assert settings.tools.grep.max_matches == 123
    assert settings.tools.grep.max_file_size_bytes == 2_000_000


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


def test_load_settings_audio_transcription_config(tmp_path: Path) -> None:
    config_file = tmp_path / "bot.toml"
    config_file.write_text(
        """
[llm]
provider = "openai"
api_key = "secret"

[channels.telegram]
bot_token = "token"

[tools.audio_transcription]
enabled = true
model = "medium"
device = "cpu"
compute_type = "int8_float16"
beam_size = 7
vad_filter = false
auto_transcribe_short_incoming = true
auto_transcribe_max_duration_seconds = 30
"""
    )

    settings = load_settings(config_file)
    assert settings.tools.audio_transcription.enabled is True
    assert settings.tools.audio_transcription.model == "medium"
    assert settings.tools.audio_transcription.device == "cpu"
    assert settings.tools.audio_transcription.compute_type == "int8_float16"
    assert settings.tools.audio_transcription.beam_size == 7
    assert settings.tools.audio_transcription.vad_filter is False
    assert settings.tools.audio_transcription.auto_transcribe_short_incoming is True
    assert settings.tools.audio_transcription.auto_transcribe_max_duration_seconds == 30


def test_load_settings_rejects_invalid_xai_limits(tmp_path: Path) -> None:
    config_file = tmp_path / "bot.toml"
    config_file.write_text(
        """
[llm]
provider = "openai_responses"
api_key = "secret"

[llm.xai]
web_search_enabled = true

[llm.xai.web_search]
allowed_domains = ["a.com", "b.com", "c.com", "d.com", "e.com", "f.com"]

[channels.telegram]
bot_token = "token"
"""
    )

    with pytest.raises(ValueError):
        load_settings(config_file)


def test_load_settings_from_lua_file(tmp_path: Path) -> None:
    pytest.importorskip("lupa")

    config_file = tmp_path / "bot.lua"
    config_file.write_text(
        """
return {
  runtime = {
    log_level = "DEBUG",
    agent_timeout_seconds = 180,
  },
  llm = {
    provider = "openai",
    api_key = "secret",
    http2 = true,
    request_timeout_seconds = 50,
    sock_connect_timeout_seconds = 12,
    sock_read_timeout_seconds = 50,
    retry_attempts = 4,
    retry_delay_seconds = 2.0,
    main_responses_state_mode = "full_messages",
    agent_responses_state_mode = "previous_response_id",
    prompt_cache_enabled = false,
    prompt_cache_retention = "24h",
    openrouter = {
      models = { "anthropic/claude-3.5-sonnet", "gryphe/mythomax-l2-13b" },
      plugins = {
        {
          id = "file-parser",
          pdf = {
            engine = "pdf-text",
          },
        },
      },
      provider = {
        order = { "anthropic", "openai" },
        allow_fallbacks = true,
        data_collection = "deny",
        provider_extra = {
          custom_hint = "value",
        },
      },
    },
  },
  channels = {
    telegram = {
      bot_token = "token",
      allowed_chat_ids = {},
      allowed_user_ids = {},
      allowed_document_mime_types = {},
    },
  },
  memory = {
    context_ratio_before_compact = 0.9,
  },
}
""",
        encoding="utf-8",
    )

    settings = load_settings(config_file)
    assert settings.runtime.log_level == "DEBUG"
    assert settings.runtime.agent_timeout_seconds == 180
    assert settings.llm.api_key == "secret"
    assert settings.llm.http2 is True
    assert settings.llm.request_timeout_seconds == 50
    assert settings.llm.sock_connect_timeout_seconds == 12
    assert settings.llm.sock_read_timeout_seconds == 50
    assert settings.llm.retry_attempts == 4
    assert settings.llm.retry_delay_seconds == 2.0
    assert settings.llm.main_responses_state_mode == "full_messages"
    assert settings.llm.agent_responses_state_mode == "previous_response_id"
    assert settings.llm.prompt_cache_enabled is False
    assert settings.llm.prompt_cache_retention == "24h"
    assert settings.llm.openrouter.models == ["anthropic/claude-3.5-sonnet", "gryphe/mythomax-l2-13b"]
    assert settings.llm.openrouter.plugins == [{"id": "file-parser", "pdf": {"engine": "pdf-text"}}]
    assert settings.llm.openrouter.provider is not None
    assert settings.llm.openrouter.provider.order == ["anthropic", "openai"]
    assert settings.llm.openrouter.provider.provider_extra["custom_hint"] == "value"
    assert settings.channels["telegram"].bot_token == "token"
    assert settings.channels["telegram"].allowed_chat_ids == []
    assert settings.memory.context_ratio_before_compact == 0.9


def test_load_settings_prefers_default_lua_when_toml_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("lupa")

    config_file = tmp_path / "config.lua"
    config_file.write_text(
        """
return {
  llm = {
    provider = "openai",
    api_key = "lua-secret",
  },
  channels = {
    telegram = {
      bot_token = "token",
    },
  },
}
""",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINIBOT_CONFIG", raising=False)

    settings = load_settings()
    assert settings.llm.api_key == "lua-secret"
    assert settings.channels["telegram"].bot_token == "token"


def test_load_settings_skips_default_directory_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("lupa")

    (tmp_path / "config.toml").mkdir()
    config_file = tmp_path / "config.lua"
    config_file.write_text(
        """
return {
  llm = {
    provider = "openai",
    api_key = "lua-secret",
  },
  channels = {
    telegram = {
      bot_token = "token",
    },
  },
}
""",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINIBOT_CONFIG", raising=False)

    settings = load_settings()
    assert settings.llm.api_key == "lua-secret"
    assert settings.channels["telegram"].bot_token == "token"


def test_load_settings_rejects_directory_for_explicit_path(tmp_path: Path) -> None:
    config_dir = tmp_path / "config.toml"
    config_dir.mkdir()

    with pytest.raises(ValueError, match="config path must be a file"):
        load_settings(config_dir)


def test_load_settings_skips_default_lua_when_lupa_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "config.lua"
    config_file.write_text("return {}", encoding="utf-8")

    real_import_module = lua_runtime.importlib.import_module

    def _import_module(name: str):
        if name == "lupa":
            raise ModuleNotFoundError(name)
        return real_import_module(name)

    monkeypatch.setattr(lua_runtime.importlib, "import_module", _import_module)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINIBOT_CONFIG", raising=False)

    settings = load_settings()
    assert settings == Settings()


def test_load_settings_lua_requires_lupa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "bot.lua"
    config_file.write_text("return {}", encoding="utf-8")

    real_import_module = lua_runtime.importlib.import_module

    def _import_module(name: str):
        if name == "lupa":
            raise ModuleNotFoundError(name)
        return real_import_module(name)

    monkeypatch.setattr(lua_runtime.importlib, "import_module", _import_module)

    with pytest.raises(RuntimeError, match="poetry install --extras lua"):
        load_settings(config_file)


def test_load_settings_rejects_ambiguous_lua_table(tmp_path: Path) -> None:
    pytest.importorskip("lupa")

    config_file = tmp_path / "bot.lua"
    config_file.write_text(
        """
return {
  channels = {
    telegram = {
      bot_token = "token",
    },
  },
  llm = {
    provider = "openai",
  },
  providers = {
    openai = {
      api_key = "secret",
    },
  },
  tools = {
    mcp = {
      servers = {
        {
          name = "bad",
          env = {
            TEST = "ok",
            [1] = "not-allowed",
          },
        },
      },
    },
  },
}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="either string keys or consecutive integer keys"):
        load_settings(config_file)
