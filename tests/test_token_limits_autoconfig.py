from __future__ import annotations

import logging

import pytest

from minibot.adapters.config.schema import ProviderConfig, Settings
import minibot.app.token_limits_autoconfig as token_limits_autoconfig


def test_resolve_limits_returns_provider_scoped_values() -> None:
    payload = {
        "openai": {
            "models": {
                "gpt-4.1-mini": {
                    "limit": {
                        "context": 1047576,
                        "output": 32768,
                    }
                }
            }
        },
        "openrouter": {
            "models": {
                "openai/gpt-4.1-mini": {
                    "limit": {
                        "context": 1000000,
                        "output": 30000,
                    }
                }
            }
        },
    }

    result = token_limits_autoconfig._resolve_limits(
        payload=payload,
        provider_name="openai",
        model_name="gpt-4.1-mini",
        base_url=None,
    )

    assert result == {"catalog_provider": "openai", "context": 1047576, "output": 32768}


def test_resolve_limits_returns_none_when_provider_misses_even_if_other_providers_have_model() -> None:
    payload = {
        "openai": {
            "models": {
                "gpt-4.1-mini": {
                    "limit": {
                        "context": 1047576,
                        "output": 32768,
                    }
                }
            }
        }
    }

    result = token_limits_autoconfig._resolve_limits(
        payload=payload,
        provider_name="custom_openai_proxy",
        model_name="gpt-4.1-mini",
        base_url="https://proxy.example/v1",
    )

    assert result is None


def test_apply_runtime_token_autoconfig_keeps_config_when_provider_miss(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    payload = {
        "openai": {
            "models": {
                "gpt-4.1-mini": {
                    "limit": {
                        "context": 1047576,
                        "output": 32768,
                    }
                }
            }
        }
    }
    settings = Settings()
    settings.llm.provider = "custom_openai_proxy"
    settings.llm.model = "gpt-4.1-mini"
    settings.llm.max_new_tokens = 1234
    settings.memory.max_history_tokens = 4321

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", lambda _logger: payload)

    logger = logging.getLogger("test.token_limits.provider_miss")
    with caplog.at_level(logging.WARNING):
        token_limits_autoconfig.apply_runtime_token_autoconfig(settings=settings, agent_specs=[], logger=logger)

    assert settings.llm.max_new_tokens == 1234
    assert settings.memory.max_history_tokens == 4321
    assert "keeping configured values" in caplog.text


def test_apply_runtime_token_autoconfig_uses_xai_base_url_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "xai": {
            "models": {
                "grok-4-1-fast-reasoning": {
                    "limit": {
                        "context": 2000000,
                        "output": 30000,
                    }
                }
            }
        }
    }
    settings = Settings()
    settings.llm.provider = "openai"
    settings.llm.model = "grok-4-1-fast-reasoning"
    settings.providers["openai"] = ProviderConfig(api_key="dummy", base_url="https://api.x.ai/v1")

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", lambda _logger: payload)

    logger = logging.getLogger("test.token_limits.xai_alias")
    token_limits_autoconfig.apply_runtime_token_autoconfig(settings=settings, agent_specs=[], logger=logger)

    assert settings.memory.max_history_tokens == 1_900_000
    assert settings.llm.max_new_tokens == 30_000
