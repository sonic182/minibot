from __future__ import annotations

import logging

import pytest

from minibot.adapters.config.schema import LLMMConfig, Settings


def _reset_container(module) -> None:
    module.AppContainer._settings = None
    module.AppContainer._logger = None
    module.AppContainer._event_bus = None
    module.AppContainer._memory_backend = None
    module.AppContainer._llm_client = None
    module.AppContainer._llm_factory = None
    module.AppContainer._agent_registry = None
    module.AppContainer._skill_registry = None
    module.AppContainer._extensions = None
    module.AppContainer._token_autoconfig_applied = False


def test_app_container_getters_fail_when_not_configured() -> None:
    from minibot.adapters.container import app_container

    _reset_container(app_container)

    with pytest.raises(RuntimeError):
        app_container.AppContainer.get_settings()
    with pytest.raises(RuntimeError):
        app_container.AppContainer.get_logger()
    with pytest.raises(RuntimeError):
        app_container.AppContainer.get_event_bus()
    with pytest.raises(RuntimeError):
        app_container.AppContainer.get_memory_backend()
    with pytest.raises(RuntimeError):
        app_container.AppContainer.get_llm_client()


@pytest.mark.asyncio
async def test_app_container_configures_and_initializes_core_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.adapters.container import app_container

    _reset_container(app_container)

    class _Backend:
        def __init__(self, *_args, **_kwargs) -> None:
            self.initialized = False

        async def initialize(self) -> None:
            self.initialized = True

    class _LLMFactory:
        def __init__(self, _settings) -> None:
            self._client = object()

        def create_default(self):
            return self._client

    settings = Settings(llm=LLMMConfig(api_key="secret"))
    monkeypatch.setattr(app_container, "load_settings", lambda *_: settings)
    monkeypatch.setattr(app_container, "configure_logging", lambda *_: logging.getLogger("test.container"))
    monkeypatch.setattr(app_container, "SQLAlchemyMemoryBackend", _Backend)
    monkeypatch.setattr(app_container, "PendingTurnStore", _Backend)
    monkeypatch.setattr(app_container, "LLMClientFactory", _LLMFactory)
    monkeypatch.setattr(app_container, "load_agent_specs", lambda *_: [])

    app_container.AppContainer.configure()
    await app_container.AppContainer.initialize_storage()

    assert app_container.AppContainer.get_memory_backend().initialized is True
    assert app_container.AppContainer.get_extensions().names()
