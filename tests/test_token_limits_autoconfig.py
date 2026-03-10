from __future__ import annotations

import logging

import pytest

from minibot.adapters.config.schema import ProviderConfig, Settings
import minibot.app.token_limits_autoconfig as token_limits_autoconfig


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    async def content(self) -> bytes:
        return self._payload.encode("utf-8")


class _FakeHTTPClient:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    async def get(self, _url: str, headers: dict[str, str]) -> _FakeResponse:
        assert headers["Accept"] == "application/json"
        return _FakeResponse(self._payload)


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

    async def _fetch(_logger):
        return payload

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", _fetch)

    logger = logging.getLogger("test.token_limits.provider_miss")
    with caplog.at_level(logging.WARNING):
        token_limits_autoconfig.apply_runtime_token_autoconfig(settings=settings, agent_specs=[], logger=logger)

    assert settings.llm.max_new_tokens == 1234
    assert settings.memory.max_history_tokens == 4321
    assert "keeping configured values" in caplog.text


@pytest.mark.asyncio
async def test_apply_runtime_token_autoconfig_async_keeps_config_when_provider_miss(
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

    async def _fetch(_logger):
        return payload

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", _fetch)

    logger = logging.getLogger("test.token_limits.provider_miss.async")
    with caplog.at_level(logging.WARNING):
        await token_limits_autoconfig.apply_runtime_token_autoconfig_async(
            settings=settings,
            agent_specs=[],
            logger=logger,
        )

    assert settings.llm.max_new_tokens == 1234
    assert settings.memory.max_history_tokens == 4321
    assert "keeping configured values" in caplog.text


@pytest.mark.asyncio
async def test_apply_runtime_token_autoconfig_uses_xai_base_url_alias(monkeypatch: pytest.MonkeyPatch) -> None:
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

    async def _fetch(_logger):
        return payload

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", _fetch)

    logger = logging.getLogger("test.token_limits.xai_alias")
    await token_limits_autoconfig.apply_runtime_token_autoconfig_async(
        settings=settings,
        agent_specs=[],
        logger=logger,
    )

    assert settings.memory.max_history_tokens == 1_900_000
    assert settings.llm.max_new_tokens == 30_000


@pytest.mark.asyncio
async def test_fetch_models_catalog_uses_aiosonic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        token_limits_autoconfig.aiosonic,
        "HTTPClient",
        lambda: _FakeHTTPClient('{"openai":{"models":{}}}'),
    )
    logger = logging.getLogger("test.token_limits.fetch")
    payload = await token_limits_autoconfig._fetch_models_catalog(logger)
    assert payload == {"openai": {"models": {}}}


@pytest.mark.asyncio
async def test_fetch_models_catalog_returns_none_for_non_object_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        token_limits_autoconfig.aiosonic,
        "HTTPClient",
        lambda: _FakeHTTPClient('["not-an-object"]'),
    )
    logger = logging.getLogger("test.token_limits.fetch_invalid")
    payload = await token_limits_autoconfig._fetch_models_catalog(logger)
    assert payload is None
