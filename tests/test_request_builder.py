from __future__ import annotations

from minibot.llm.services.request_builder import RequestContext, openrouter_kwargs


def _ctx(
    *,
    provider_name: str = "openrouter",
    is_responses_provider: bool = False,
    reasoning_effort: str | None = None,
    openrouter_reasoning_enabled: bool | None = None,
) -> RequestContext:
    return RequestContext(
        model="x",
        provider_name=provider_name,
        is_responses_provider=is_responses_provider,
        temperature=None,
        max_new_tokens=None,
        prompt_cache_enabled=True,
        prompt_cache_retention=None,
        reasoning_effort=reasoning_effort,
        openrouter_models=(),
        openrouter_provider={},
        openrouter_reasoning_enabled=openrouter_reasoning_enabled,
        openrouter_plugins=(),
    )


def test_openrouter_kwargs_sets_enabled_with_effort() -> None:
    kwargs = openrouter_kwargs(_ctx(reasoning_effort="high"))

    assert kwargs["reasoning"] == {"effort": "high", "enabled": True}


def test_openrouter_kwargs_sets_enabled_from_flag_only() -> None:
    kwargs = openrouter_kwargs(_ctx(reasoning_effort=None, openrouter_reasoning_enabled=True))

    assert kwargs["reasoning"] == {"enabled": True}


def test_openrouter_kwargs_omits_reasoning_when_not_configured() -> None:
    kwargs = openrouter_kwargs(_ctx(reasoning_effort=None, openrouter_reasoning_enabled=None))

    assert "reasoning" not in kwargs
