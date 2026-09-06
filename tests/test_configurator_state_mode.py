from __future__ import annotations

from typing import Any

import pytest

from minibot.adapters.config import configurator


@pytest.mark.parametrize(
    ("target", "use_responses_api", "expected_provider", "expected_mode"),
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
    expected_provider: str,
    expected_mode: str,
) -> None:
    written: dict[tuple[str, ...], Any] = {}

    monkeypatch.setattr(configurator, "_current_llm_target", lambda _: target)
    monkeypatch.setattr(configurator, "_ask_llm_target", lambda _: target)
    monkeypatch.setattr(configurator, "_ask_bool", lambda *_, **__: use_responses_api)
    monkeypatch.setattr(configurator, "_ask_secret", lambda *_, **__: "key")
    monkeypatch.setattr(configurator, "_ask_model", lambda *_, **__: "some-model")
    monkeypatch.setattr(configurator, "_set_value", lambda _doc, path, value: written.__setitem__(path, value))

    configurator._configure_llm(object(), configurator.Settings())

    assert written[("llm", "provider")] == expected_provider
    assert written[("llm", "main_responses_state_mode")] == expected_mode
    assert written[("llm", "agent_responses_state_mode")] == expected_mode


def test_wizard_makes_enabled_skills_visible_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A skill catalog the model never sees is a skill it never uses."""
    written: dict[tuple[str, ...], Any] = {}

    monkeypatch.setattr(configurator, "_write", lambda *_, **__: None)
    monkeypatch.setattr(configurator, "_ask_multiselect", lambda *_, **__: {"skills", "files"})
    monkeypatch.setattr(configurator, "_set_value", lambda _doc, path, value: written.__setitem__(path, value))

    configurator._configure_tools(object(), configurator.Settings())

    assert written[("tools", "skills", "enabled")] is True
    assert written[("tools", "skills", "preload_catalog")] is True


def test_spill_default_catches_bash_because_it_has_no_spill_of_its_own() -> None:
    excluded = configurator.Settings().tools.tool_output_spill.exclude_tools

    assert "bash" not in excluded
    # http_request runs its own spill, pre_response is signalling
    assert set(excluded) == {"http_request", "pre_response"}
