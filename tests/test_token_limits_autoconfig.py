from __future__ import annotations

import logging
from pathlib import Path

import pytest

import minibot.app.token_limits_autoconfig as token_limits_autoconfig
from minibot.adapters.config.schema import ProviderConfig, Settings
from minibot.core.agents import AgentSpec


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


@pytest.mark.asyncio
async def test_resolve_limits_returns_provider_scoped_values() -> None:
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

    result = await token_limits_autoconfig._resolve_limits(
        payload=payload,
        provider_name="openai",
        model_name="gpt-4.1-mini",
        base_url=None,
        auth_path=None,
        logger=logging.getLogger("test.token_limits.resolve_limits"),
    )

    assert result == {"catalog_provider": "openai", "context": 1047576, "output": 32768}


@pytest.mark.asyncio
async def test_resolve_limits_returns_none_when_provider_misses_even_if_other_providers_have_model() -> None:
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

    result = await token_limits_autoconfig._resolve_limits(
        payload=payload,
        provider_name="custom_openai_proxy",
        model_name="gpt-4.1-mini",
        base_url="https://proxy.example/v1",
        auth_path=None,
        logger=logging.getLogger("test.token_limits.resolve_limits_miss"),
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
async def test_apply_runtime_token_autoconfig_agent_not_capped_by_main_model_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "openai": {
            "models": {
                "gpt-4.1-mini": {"limit": {"context": 1047576, "output": 32768}},
            }
        },
        "anthropic": {
            "models": {
                "claude-long": {"limit": {"context": 2000000, "output": 64000}},
            }
        },
    }
    settings = Settings()
    settings.llm.provider = "openai"
    settings.llm.model = "gpt-4.1-mini"
    settings.llm.max_new_tokens = 1234

    spec = AgentSpec(
        name="researcher",
        description="research specialist",
        system_prompt="research things",
        source_path=Path("agents/researcher.md"),
        model_provider="anthropic",
        model="claude-long",
    )

    async def _fetch(_logger):
        return payload

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", _fetch)

    logger = logging.getLogger("test.token_limits.agent_not_capped")
    adjusted_specs = await token_limits_autoconfig.apply_runtime_token_autoconfig_async(
        settings=settings,
        agent_specs=[spec],
        logger=logger,
    )

    assert len(adjusted_specs) == 1
    # The agent's own model output ceiling (64000) wins, unaffected by the main model's
    # much lower configured max_new_tokens (1234).
    assert adjusted_specs[0].max_new_tokens == 64000


@pytest.mark.asyncio
async def test_apply_runtime_token_autoconfig_respects_agent_own_max_new_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "anthropic": {
            "models": {
                "claude-long": {"limit": {"context": 2000000, "output": 64000}},
            }
        },
    }
    settings = Settings()
    settings.llm.provider = "anthropic"
    settings.llm.model = "claude-long"
    settings.llm.max_new_tokens = 1234

    spec = AgentSpec(
        name="researcher",
        description="research specialist",
        system_prompt="research things",
        source_path=Path("agents/researcher.md"),
        model_provider="anthropic",
        model="claude-long",
        max_new_tokens=500,
    )

    async def _fetch(_logger):
        return payload

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", _fetch)

    logger = logging.getLogger("test.token_limits.agent_own_cap")
    adjusted_specs = await token_limits_autoconfig.apply_runtime_token_autoconfig_async(
        settings=settings,
        agent_specs=[spec],
        logger=logger,
    )

    assert adjusted_specs[0].max_new_tokens == 500


@pytest.mark.allow_catalog_fetch
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


@pytest.mark.allow_catalog_fetch
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


_FIREWORKS_CATALOG = {
    "fireworks-ai": {"models": {"deepseek-v4p1": {"limit": {"context": 163840, "output": 16384}}}},
}


def _fireworks_settings() -> Settings:
    settings = Settings()
    settings.providers["fireworks"] = ProviderConfig(
        api_key="k",
        api_format="openai",
        base_url="https://api.fireworks.ai/inference/v1",
        models=["deepseek-v4p1"],
    )
    return settings


@pytest.mark.asyncio
async def test_ensure_model_limits_caches_the_resolved_entry_not_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetches = 0

    async def _catalog(_logger: object) -> dict[str, object]:
        nonlocal fetches
        fetches += 1
        return _FIREWORKS_CATALOG

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", _catalog)
    logger = logging.getLogger("test.token_limits.ensure")
    kwargs = {"settings": _fireworks_settings(), "provider_name": "fireworks", "model_name": "deepseek-v4p1"}

    first = await token_limits_autoconfig.ensure_model_limits(logger=logger, **kwargs)
    second = await token_limits_autoconfig.ensure_model_limits(logger=logger, **kwargs)

    assert first == second == {"catalog_provider": "fireworks-ai", "context": 163840, "output": 16384}
    assert fetches == 1
    assert token_limits_autoconfig.cached_model_limits("fireworks", "deepseek-v4p1") == first


@pytest.mark.asyncio
async def test_ensure_model_limits_caches_an_uncatalogued_model_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a sentinel, `cache.get` can't tell a stored None from a miss and every delegation
    to an unknown model would re-download the catalog."""
    fetches = 0

    async def _catalog(_logger: object) -> dict[str, object]:
        nonlocal fetches
        fetches += 1
        return _FIREWORKS_CATALOG

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", _catalog)
    logger = logging.getLogger("test.token_limits.ensure_missing")
    kwargs = {"settings": _fireworks_settings(), "provider_name": "fireworks", "model_name": "who-knows"}

    assert await token_limits_autoconfig.ensure_model_limits(logger=logger, **kwargs) is None
    assert await token_limits_autoconfig.ensure_model_limits(logger=logger, **kwargs) is None
    assert fetches == 1


@pytest.mark.asyncio
async def test_ensure_model_limits_does_not_cache_a_failed_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caching a network failure would pin 'no compaction' for the whole ttl."""
    payloads: list[dict[str, object] | None] = [None, _FIREWORKS_CATALOG]

    async def _catalog(_logger: object) -> dict[str, object] | None:
        return payloads.pop(0)

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", _catalog)
    logger = logging.getLogger("test.token_limits.ensure_failed")
    kwargs = {"settings": _fireworks_settings(), "provider_name": "fireworks", "model_name": "deepseek-v4p1"}

    assert await token_limits_autoconfig.ensure_model_limits(logger=logger, **kwargs) is None
    assert await token_limits_autoconfig.ensure_model_limits(logger=logger, **kwargs) is not None


@pytest.mark.asyncio
async def test_startup_prewarms_every_model_a_delegation_may_target(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _catalog(_logger: object) -> dict[str, object]:
        return _FIREWORKS_CATALOG

    monkeypatch.setattr(token_limits_autoconfig, "_fetch_models_catalog", _catalog)

    await token_limits_autoconfig.apply_runtime_token_autoconfig_async(
        settings=_fireworks_settings(),
        agent_specs=[],
        logger=logging.getLogger("test.token_limits.prewarm"),
    )

    # No agent spec named it, so only the providers pass could have cached it.
    assert token_limits_autoconfig.cached_model_limits("fireworks", "deepseek-v4p1") == {
        "catalog_provider": "fireworks-ai",
        "context": 163840,
        "output": 16384,
    }


@pytest.mark.asyncio
async def test_resolve_limits_maps_fireworks_base_url_to_its_catalog_key() -> None:
    # models.dev files Fireworks under "fireworks-ai"; the `[providers.fireworks]` section name
    # alone finds nothing.
    payload = {
        "fireworks-ai": {
            "models": {
                "accounts/fireworks/models/deepseek-v4p1-flash": {"limit": {"context": 163840, "output": 16384}}
            }
        }
    }

    result = await token_limits_autoconfig._resolve_limits(
        payload=payload,
        provider_name="fireworks",
        model_name="accounts/fireworks/models/deepseek-v4p1-flash",
        base_url="https://api.fireworks.ai/inference/v1",
        auth_path=None,
        logger=logging.getLogger("test.token_limits.fireworks"),
    )

    assert result == {"catalog_provider": "fireworks-ai", "context": 163840, "output": 16384}
