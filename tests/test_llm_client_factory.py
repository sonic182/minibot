from __future__ import annotations

from pathlib import Path

from minibot.adapters.config.schema import (
    LLMMConfig,
    OpenRouterLLMConfig,
    OpenRouterProviderRoutingConfig,
    ProviderConfig,
    Settings,
)
from minibot.app.llm_client_factory import LLMClientFactory
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

    created_configs: list[LLMMConfig] = []

    class _FakeClient:
        def __init__(self, config: LLMMConfig) -> None:
            created_configs.append(config.model_copy(deep=True))

    monkeypatch.setattr("minibot.app.llm_client_factory.LLMClient", _FakeClient)

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
        providers={"anthropic": ProviderConfig(api_key="anthropic-key", base_url="https://anthropic.local")},
    )
    factory = LLMClientFactory(settings)

    created_configs: list[LLMMConfig] = []

    class _FakeClient:
        def __init__(self, config: LLMMConfig) -> None:
            created_configs.append(config.model_copy(deep=True))

    monkeypatch.setattr("minibot.app.llm_client_factory.LLMClient", _FakeClient)

    agent = _agent_spec(name="worker", model_provider="anthropic", model="claude-sonnet")
    factory.create_for_agent(agent)

    assert len(created_configs) == 1
    assert created_configs[0].provider == "anthropic"
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

    created_configs: list[LLMMConfig] = []

    class _FakeClient:
        def __init__(self, config: LLMMConfig) -> None:
            created_configs.append(config.model_copy(deep=True))

    monkeypatch.setattr("minibot.app.llm_client_factory.LLMClient", _FakeClient)

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

    created_configs: list[LLMMConfig] = []

    class _FakeClient:
        def __init__(self, config: LLMMConfig) -> None:
            created_configs.append(config.model_copy(deep=True))

    monkeypatch.setattr("minibot.app.llm_client_factory.LLMClient", _FakeClient)

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

    created_configs: list[LLMMConfig] = []

    class _FakeClient:
        def __init__(self, config: LLMMConfig) -> None:
            created_configs.append(config.model_copy(deep=True))

    monkeypatch.setattr("minibot.app.llm_client_factory.LLMClient", _FakeClient)

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
