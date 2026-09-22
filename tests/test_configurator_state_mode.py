from __future__ import annotations

from typing import Any

import pytest

from minibot.adapters.config import configurator


def _stub_wizard(
    monkeypatch: pytest.MonkeyPatch,
    *,
    targets: set[str],
    use_responses_api: bool = False,
    main_target: str | None = None,
    models: list[str] | None = None,
) -> tuple[dict[tuple[str, ...], Any], set[tuple[str, ...]]]:
    written: dict[tuple[str, ...], Any] = {}
    removed: set[tuple[str, ...]] = set()

    monkeypatch.setattr(configurator, "_write", lambda *_, **__: None)
    monkeypatch.setattr(configurator, "_ask_multiselect", lambda *_, **__: targets)
    monkeypatch.setattr(configurator, "_ask_bool", lambda *_, **__: use_responses_api)
    monkeypatch.setattr(configurator, "_ask_secret", lambda *_, **__: "key")
    monkeypatch.setattr(configurator, "_provider_models", lambda *_, **__: list(models or ["some-model"]))
    monkeypatch.setattr(configurator, "_ask_models", lambda available, _current: list(available))
    monkeypatch.setattr(configurator, "_choose_model", lambda *_, **__: "some-model")
    monkeypatch.setattr(
        configurator,
        "_ask_main_provider",
        lambda configured, _default: main_target or next(iter(configured)),
    )
    monkeypatch.setattr(configurator, "_set_value", lambda _doc, path, value: written.__setitem__(path, value))
    monkeypatch.setattr(configurator, "_unset_value", lambda _doc, path: removed.add(path))
    return written, removed


@pytest.mark.parametrize(
    ("target", "use_responses_api", "expected_api_format", "expected_mode"),
    [
        # Responses API keeps turn state server-side, so a tool loop can send just the delta.
        ("opencode_go", True, "openai_responses", "previous_response_id"),
        # Chat Completions is stateless and resends the history regardless of the setting.
        ("opencode_go", False, "openai", "full_messages"),
        ("openai", False, "openai", "full_messages"),
        # xai maps straight onto openai_responses without the opt-in question
        ("xai", False, "openai_responses", "previous_response_id"),
    ],
)
def test_wizard_picks_the_state_mode_the_provider_can_actually_use(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    use_responses_api: bool,
    expected_api_format: str,
    expected_mode: str,
) -> None:
    monkeypatch.setattr(configurator, "_current_llm_target", lambda _: target)
    written, removed = _stub_wizard(monkeypatch, targets={target}, use_responses_api=use_responses_api)

    configurator._configure_llm(object(), configurator.Settings())

    # The main agent references the section name, and the section declares the API it speaks.
    assert written[("llm", "provider")] == target
    assert written[("providers", target, "api_format")] == expected_api_format
    assert written[("llm", "main_responses_state_mode")] == expected_mode
    assert written[("llm", "agent_responses_state_mode")] == expected_mode

    # OpenCode Go rejects requests without a session id; every other target must not carry a stale one.
    session_header = ("providers", target, "headers", "x-opencode-session")
    if target == "opencode_go":
        assert written[session_header] == "minibot"
    else:
        assert session_header not in written
        assert session_header in removed


def test_wizard_configures_several_providers_and_one_main_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delegation to another provider only works if more than one is set up."""
    monkeypatch.setattr(configurator, "_current_llm_target", lambda _: "openai")
    written, _ = _stub_wizard(
        monkeypatch,
        targets={"openai", "opencode_go"},
        main_target="opencode_go",
        models=["deepseek-v3.6", "mimo-v2.5"],
    )

    configurator._configure_llm(object(), configurator.Settings())

    assert written[("providers", "openai", "api_format")] == "openai"
    assert written[("providers", "opencode_go", "api_format")] == "openai"
    assert written[("providers", "opencode_go", "base_url")] == "https://opencode.ai/zen/go/v1"
    assert written[("providers", "opencode_go", "models")] == ["deepseek-v3.6", "mimo-v2.5"]
    assert written[("llm", "provider")] == "opencode_go"


def test_wizard_moves_a_third_party_endpoint_out_of_its_format_named_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-alias wizard put z.ai in [providers.openai]; leaving it there fakes a provider."""
    settings = configurator.Settings(
        providers={
            "openai": configurator.ProviderConfig(
                api_key="zai-key",
                base_url="https://api.z.ai/api/coding/paas/v4",
            )
        }
    )
    monkeypatch.setattr(configurator, "_current_llm_target", lambda _: "zai")
    written, removed = _stub_wizard(monkeypatch, targets={"zai"})

    configurator._configure_llm(object(), settings)

    assert ("providers", "openai", "api_key") in removed
    assert ("providers", "openai", "base_url") in removed
    assert written[("providers", "zai", "base_url")] == "https://api.z.ai/api/coding/paas/v4"
    assert written[("providers", "zai", "api_format")] == "openai"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", ["gpt-5.6-luna", "deepseek-v3.6", "glm-5.3"]),
        ("GLM", ["glm-5.3"]),
        ("deep 3.6", ["deepseek-v3.6"]),
        ("nothing-here", []),
    ],
)
def test_filter_models_narrows_a_long_catalog(query: str, expected: list[str]) -> None:
    models = ["gpt-5.6-luna", "deepseek-v3.6", "glm-5.3"]

    assert configurator._filter_models(models, query) == expected


def test_wizard_makes_enabled_skills_visible_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A skill catalog the model never sees is a skill it never uses."""
    written: dict[tuple[str, ...], Any] = {}

    monkeypatch.setattr(configurator, "_write", lambda *_, **__: None)
    monkeypatch.setattr(configurator, "_ask_multiselect", lambda *_, **__: {"skills", "files"})
    monkeypatch.setattr(configurator, "_ask_bool", lambda *_, **__: True)
    monkeypatch.setattr(configurator, "_set_value", lambda _doc, path, value: written.__setitem__(path, value))

    configurator._configure_tools(object(), configurator.Settings())

    assert written[("tools", "skills", "enabled")] is True
    assert written[("tools", "skills", "preload_catalog")] is True
    assert written[("tools", "skills", "install")] is True


def test_spill_default_catches_bash_because_it_has_no_spill_of_its_own() -> None:
    excluded = configurator.Settings().tools.tool_output_spill.exclude_tools

    assert "bash" not in excluded
    # http_request runs its own spill, pre_response is signalling
    assert set(excluded) == {"http_request", "pre_response"}


@pytest.mark.parametrize(
    ("backend", "prompted_key", "expected_default", "skipped_key"),
    [
        (
            "sqlite",
            ("tools", "rag", "sqlite_url"),
            "sqlite+aiosqlite:///./data/rag.db",
            ("tools", "rag", "qdrant_url"),
        ),
        ("qdrant", ("tools", "rag", "qdrant_url"), "http://localhost:6333", ("tools", "rag", "sqlite_url")),
    ],
)
def test_wizard_asks_only_the_location_the_chosen_rag_backend_reads(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    prompted_key: tuple[str, ...],
    expected_default: str,
    skipped_key: tuple[str, ...],
) -> None:
    """Prompting for the other backend's location would write a value nothing reads."""
    written: dict[tuple[str, ...], Any] = {}
    offered: dict[str, str] = {}

    def _ask_required(label: str, value: str) -> str:
        offered[label] = value
        return value

    monkeypatch.setattr(configurator, "_write", lambda *_, **__: None)
    monkeypatch.setattr(configurator, "_ask_multiselect", lambda *_, **__: {"rag"})
    monkeypatch.setattr(configurator, "_ask_single_select", lambda *_, **__: backend)
    monkeypatch.setattr(configurator, "_ask_required", _ask_required)
    monkeypatch.setattr(configurator, "_set_value", lambda _doc, path, value: written.__setitem__(path, value))

    configurator._configure_tools(object(), configurator.Settings())

    assert written[("tools", "rag", "enabled")] is True
    assert written[("tools", "rag", "backend")] == backend
    assert written[prompted_key] == expected_default
    assert skipped_key not in written
    # The prompt offers the current value, so pressing enter keeps a working config.
    assert expected_default in offered.values()
