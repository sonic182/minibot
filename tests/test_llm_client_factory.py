from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from minibot.adapters.config.schema import (
    LLMMConfig,
    OpenRouterLLMConfig,
    OpenRouterProviderRoutingConfig,
    ProviderConfig,
    Settings,
)
from minibot.app.llm_client_factory import LLMClientFactory, available_providers, find_provider
from minibot.core.agents import AgentSpec


def _agent_spec(
    *,
    name: str,
    model_provider: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_new_tokens: int | None = None,
    reasoning_effort: str | None = None,
    max_tool_iterations: int | None = None,
    openrouter_provider_overrides: dict[str, object] | None = None,
) -> AgentSpec:
    return AgentSpec(
        name=name,
        description="test",
        system_prompt="you are test",
        source_path=Path("/tmp/agent.md"),
        model_provider=model_provider,
        model=model,
        temperature=temperature,
        max_new_tokens=max_new_tokens,
        reasoning_effort=reasoning_effort,
        max_tool_iterations=max_tool_iterations,
        openrouter_provider_overrides=openrouter_provider_overrides or {},
    )


def _patch_fake_client(monkeypatch) -> list[LLMMConfig]:
    created_configs: list[LLMMConfig] = []

    class _FakeClient:
        def __init__(self, config: LLMMConfig) -> None:
            created_configs.append(config.model_copy(deep=True))

    monkeypatch.setattr("minibot.app.llm_client_factory.LLMClient", _FakeClient)
    return created_configs


def test_create_for_agent_cache_key_includes_agent_overrides(monkeypatch) -> None:
    settings = Settings(
        llm=LLMMConfig(
            provider="openai",
            api_key="default-key",
            base_url="https://default.local",
            model="gpt-4o-mini",
        )
    )
    factory = LLMClientFactory(settings)
    created_configs = _patch_fake_client(monkeypatch)

    agent_a = _agent_spec(name="a", temperature=0.1)
    agent_b = _agent_spec(name="b", temperature=0.9)

    client_a = factory.create_for_agent(agent_a)
    client_b = factory.create_for_agent(agent_b)

    assert client_a is not client_b
    assert len(created_configs) == 2
    assert created_configs[0].temperature == 0.1
    assert created_configs[1].temperature == 0.9


def test_create_for_agent_provider_override_uses_provider_credentials(monkeypatch) -> None:
    settings = Settings(
        llm=LLMMConfig(
            provider="openai",
            api_key="openai-key",
            base_url="https://openai.local",
            model="gpt-4o-mini",
        ),
        providers={
            "anthropic": ProviderConfig(
                api_key="anthropic-key",
                base_url="https://anthropic.local",
                api_format="claude",
            )
        },
    )
    factory = LLMClientFactory(settings)

    created_configs = _patch_fake_client(monkeypatch)

    agent = _agent_spec(name="worker", model_provider="anthropic", model="claude-sonnet")
    factory.create_for_agent(agent)

    assert len(created_configs) == 1
    assert created_configs[0].provider == "claude"
    assert created_configs[0].api_key == "anthropic-key"
    assert created_configs[0].base_url == "https://anthropic.local"


def test_create_for_agent_openrouter_provider_overrides(monkeypatch) -> None:
    settings = Settings(
        llm=LLMMConfig(
            provider="openrouter",
            api_key="openrouter-key",
            model="x-ai/grok-4.1-fast",
        )
    )
    factory = LLMClientFactory(settings)

    created_configs = _patch_fake_client(monkeypatch)

    agent = _agent_spec(
        name="browser",
        model_provider="openrouter",
        openrouter_provider_overrides={
            "order": ["anthropic", "openai"],
            "allow_fallbacks": True,
            "only": ["openai", "anthropic"],
            "sort": "price",
        },
    )

    factory.create_for_agent(agent)

    assert len(created_configs) == 1
    assert created_configs[0].openrouter.provider is not None
    assert created_configs[0].openrouter.provider.order == ["anthropic", "openai"]
    assert created_configs[0].openrouter.provider.allow_fallbacks is True
    assert created_configs[0].openrouter.provider.only == ["openai", "anthropic"]
    assert created_configs[0].openrouter.provider.sort == "price"


def test_create_for_agent_cache_key_includes_openrouter_provider_overrides(monkeypatch) -> None:
    settings = Settings(
        llm=LLMMConfig(
            provider="openrouter",
            api_key="openrouter-key",
            model="x-ai/grok-4.1-fast",
        )
    )
    factory = LLMClientFactory(settings)

    created_configs = _patch_fake_client(monkeypatch)

    agent_a = _agent_spec(
        name="a",
        model_provider="openrouter",
        openrouter_provider_overrides={"only": ["openai"]},
    )
    agent_b = _agent_spec(
        name="b",
        model_provider="openrouter",
        openrouter_provider_overrides={"only": ["anthropic"]},
    )

    client_a = factory.create_for_agent(agent_a)
    client_b = factory.create_for_agent(agent_b)

    assert client_a is not client_b
    assert len(created_configs) == 2


def test_create_for_agent_openrouter_provider_overrides_merge_global(monkeypatch) -> None:
    settings = Settings(
        llm=LLMMConfig(
            provider="openrouter",
            api_key="openrouter-key",
            model="x-ai/grok-4.1-fast",
            openrouter=OpenRouterLLMConfig(
                provider=OpenRouterProviderRoutingConfig(
                    allow_fallbacks=True,
                    order=["openai", "anthropic"],
                    only=["openai"],
                )
            ),
        )
    )
    factory = LLMClientFactory(settings)

    created_configs = _patch_fake_client(monkeypatch)

    agent = _agent_spec(
        name="browser",
        model_provider="openrouter",
        openrouter_provider_overrides={"only": ["anthropic"], "sort": "price"},
    )

    factory.create_for_agent(agent)

    assert len(created_configs) == 1
    assert created_configs[0].openrouter.provider is not None
    assert created_configs[0].openrouter.provider.allow_fallbacks is True
    assert created_configs[0].openrouter.provider.order == ["openai", "anthropic"]
    assert created_configs[0].openrouter.provider.only == ["anthropic"]
    assert created_configs[0].openrouter.provider.sort == "price"


def test_factory_applies_distinct_responses_state_modes_for_main_and_agents(monkeypatch) -> None:
    settings = Settings(
        llm=LLMMConfig(
            provider="openai_responses",
            api_key="key",
            model="gpt-5-mini",
            main_responses_state_mode="full_messages",
            agent_responses_state_mode="previous_response_id",
        )
    )
    factory = LLMClientFactory(settings)

    created_configs = _patch_fake_client(monkeypatch)

    factory.create_default()
    factory.create_for_agent(_agent_spec(name="worker"))

    assert len(created_configs) == 2
    assert created_configs[0].responses_state_mode == "full_messages"
    assert created_configs[1].responses_state_mode == "previous_response_id"


def test_create_default_cache_key_includes_xai_config(monkeypatch) -> None:
    settings = Settings(
        llm=LLMMConfig(
            provider="openai_responses",
            api_key="key",
            model="grok-4-1-fast-reasoning",
            base_url="https://api.x.ai/v1",
            xai={"web_search_enabled": True, "x_search_enabled": False},
        )
    )
    factory = LLMClientFactory(settings)

    created_configs = _patch_fake_client(monkeypatch)

    client_a = factory.create_default()
    settings.llm.xai.x_search_enabled = True
    client_b = factory.create_default()

    assert client_a is not client_b
    assert len(created_configs) == 2
    assert created_configs[0].xai.x_search_enabled is False
    assert created_configs[1].xai.x_search_enabled is True


def test_provider_alias_builds_the_client_for_its_api_format(monkeypatch) -> None:
    settings = Settings(
        llm=LLMMConfig(provider="chatgpt_codex", model="gpt-5.6-sol"),
        providers={
            "opencode_go": ProviderConfig(
                api_key="go-key",
                base_url="https://opencode.ai/zen/go/v1",
                api_format="openai_responses",
                models=["deepseek-v3.6"],
            )
        },
    )
    factory = LLMClientFactory(settings)

    created_configs = _patch_fake_client(monkeypatch)

    factory.create_for_agent(_agent_spec(name="worker", model_provider="opencode_go", model="deepseek-v3.6"))

    assert len(created_configs) == 1
    config = created_configs[0]
    assert config.provider == "openai_responses"
    assert config.api_key == "go-key"
    assert config.base_url == "https://opencode.ai/zen/go/v1"
    assert config.model == "deepseek-v3.6"


def _codex_settings() -> Settings:
    return Settings(
        llm=LLMMConfig(provider="chatgpt_codex", model="gpt-5.6-sol"),
        providers={
            "opencode_go": ProviderConfig(api_key="go-key", api_format="openai_responses", models=["deepseek-v3.6"]),
            "openrouter": ProviderConfig(api_key=""),
            "chatgpt_codex": ProviderConfig(),
        },
    )


def test_available_providers_skips_sections_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import codex_setup

    monkeypatch.setattr(codex_setup, "load_credentials", lambda _path: object())
    options = available_providers(_codex_settings())

    assert [(option.name, option.api_format) for option in options] == [
        ("chatgpt_codex", "chatgpt_codex"),
        ("opencode_go", "openai_responses"),
    ]
    assert find_provider("OpenCode_Go ", options) is options[1]
    assert find_provider("openrouter", options) is None


def test_available_providers_drops_codex_without_loadable_oauth_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key-less Codex section is not a credential: queueing work on it fails only in the worker."""
    from minibot.llm.services import codex_setup

    def _missing(_path):
        raise codex_setup.CodexCredentialsError("no credentials at that path")

    monkeypatch.setattr(codex_setup, "load_credentials", _missing)
    options = available_providers(_codex_settings())

    assert [option.name for option in options] == ["opencode_go"]
    assert find_provider("chatgpt_codex", options) is None


def test_available_providers_drops_codex_when_the_extra_is_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import codex_setup

    def _no_extra(_path):
        raise codex_setup.CodexDependencyError("llm_async_codex is not installed")

    monkeypatch.setattr(codex_setup, "load_credentials", _no_extra)

    assert [option.name for option in available_providers(_codex_settings())] == ["opencode_go"]


def test_provider_section_without_a_known_name_must_declare_its_api_format() -> None:
    # Otherwise it silently resolves to the OpenAI Chat Completions client and the delegation roster
    # advertises an api_format that does not exist.
    with pytest.raises(ValidationError, match="api_format"):
        Settings(
            llm=LLMMConfig(provider="openai", api_key="key", model="gpt-4o-mini"),
            providers={"fireworks": ProviderConfig(api_key="fw-key", base_url="https://api.fireworks.ai/v1")},
        )
