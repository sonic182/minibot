from __future__ import annotations

import logging

import pytest

from minibot.adapters.config.schema import LLMMConfig, Settings


def _reset_container(module) -> None:
    module.AppContainer._settings = None
    module.AppContainer._logger = None
    module.AppContainer._event_bus = None
    module.AppContainer._memory_backend = None
    module.AppContainer._kv_memory_backend = None
    module.AppContainer._llm_client = None
    module.AppContainer._prompt_store = None
    module.AppContainer._prompt_service = None


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
async def test_app_container_configures_and_initializes_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.adapters.container import app_container

    _reset_container(app_container)

    class _AsyncBackend:
        def __init__(self, *_args, **_kwargs) -> None:
            self.initialized = False

        async def initialize(self) -> None:
            self.initialized = True

    class _SyncBackend:
        def __init__(self, *_args, **_kwargs) -> None:
            self.initialized = False

        def initialize(self) -> None:
            self.initialized = True

    class _Store(_AsyncBackend):
        pass

    class _PromptService:
        def __init__(self, repository, event_bus, config) -> None:
            self.repository = repository
            self.event_bus = event_bus
            self.config = config

    settings = Settings(llm=LLMMConfig(api_key="secret"))
    settings.tools.kv_memory.enabled = True
    settings.scheduler.prompts.enabled = True

    monkeypatch.setattr(app_container, "load_settings", lambda *_: settings)
    monkeypatch.setattr(app_container, "configure_logging", lambda *_: logging.getLogger("test.container"))
    monkeypatch.setattr(app_container, "EventBus", lambda: object())
    monkeypatch.setattr(app_container, "SQLAlchemyMemoryBackend", _AsyncBackend)
    monkeypatch.setattr(app_container, "SQLAlchemyKeyValueMemory", _AsyncBackend)
    monkeypatch.setattr(app_container, "SQLAlchemyScheduledPromptStore", _Store)
    monkeypatch.setattr(app_container, "ScheduledPromptService", _PromptService)
    monkeypatch.setattr(app_container, "LLMClient", lambda *_: object())

    app_container.AppContainer.configure()
    await app_container.AppContainer.initialize_storage()

    assert app_container.AppContainer.get_settings() is settings
    assert app_container.AppContainer.get_logger().name == "test.container"
    assert app_container.AppContainer.get_event_bus() is not None
    assert app_container.AppContainer.get_memory_backend().initialized is True
    assert app_container.AppContainer.get_kv_memory_backend() is not None
    assert app_container.AppContainer.get_scheduled_prompt_service() is not None

    sync_backend = _SyncBackend()
    await app_container.AppContainer._initialize_backend(sync_backend)
    assert sync_backend.initialized is True
