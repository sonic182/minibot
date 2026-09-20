from __future__ import annotations

from typing import Any

import pytest

from minibot.adapters.config import configurator


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, ...], Any]:
    written: dict[tuple[str, ...], Any] = {}
    monkeypatch.setattr(configurator, "_write", lambda *_, **__: None)
    monkeypatch.setattr(configurator, "_set_value", lambda _doc, path, value: written.__setitem__(path, value))
    return written


def test_wizard_only_sets_enabled_when_http_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    written = _capture(monkeypatch)
    monkeypatch.setattr(configurator, "_ask_bool", lambda *_, **__: False)

    configurator._configure_http(object(), configurator.Settings())

    assert written == {("http", "enabled"): False}


def test_wizard_binds_loopback_and_offers_an_optional_token(monkeypatch: pytest.MonkeyPatch) -> None:
    written = _capture(monkeypatch)
    monkeypatch.setattr(configurator, "_ask_bool", lambda *_, **__: True)
    monkeypatch.setattr(configurator, "_ask_required", lambda _label, value: value)
    monkeypatch.setattr(configurator, "_ask_int", lambda _label, value: value)
    monkeypatch.setattr(configurator, "_ask_secret", lambda *_, **__: "token")

    configurator._configure_http(object(), configurator.Settings())

    assert written[("http", "enabled")] is True
    assert written[("http", "host")] == "127.0.0.1"
    assert written[("http", "port")] == 8080
    assert written[("http", "auth_token")] == "token"


def test_wizard_requires_a_token_beyond_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    written = _capture(monkeypatch)
    prompted: list[str] = []
    monkeypatch.setattr(configurator, "_ask_bool", lambda *_, **__: True)
    monkeypatch.setattr(configurator, "_ask_required", lambda _label, _value: "0.0.0.0")
    monkeypatch.setattr(configurator, "_ask_int", lambda _label, value: value)
    monkeypatch.setattr(configurator, "_ask_required_secret", lambda label, _value: prompted.append(label) or "token")

    configurator._configure_http(object(), configurator.Settings())

    assert written[("http", "host")] == "0.0.0.0"
    assert written[("http", "auth_token")] == "token"
    assert prompted == ["Auth token"]


def test_wizard_keeps_existing_basic_auth_beyond_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    written = _capture(monkeypatch)
    settings = configurator.Settings()
    settings.http.basic_auth_user = "admin"
    settings.http.basic_auth_password = "pw"
    monkeypatch.setattr(configurator, "_ask_bool", lambda *_, **__: True)
    monkeypatch.setattr(configurator, "_ask_required", lambda _label, _value: "0.0.0.0")
    monkeypatch.setattr(configurator, "_ask_int", lambda _label, value: value)
    monkeypatch.setattr(configurator, "_ask_secret", lambda *_, **__: "kept")
    monkeypatch.setattr(configurator, "_ask_required_secret", lambda *_, **__: pytest.fail("token must be optional"))

    configurator._configure_http(object(), settings)

    assert written[("http", "auth_token")] == "kept"
