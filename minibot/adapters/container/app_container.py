from __future__ import annotations

import logging
import inspect
from pathlib import Path
from typing import Optional

from minibot.app.agent_definitions_loader import load_agent_specs
from minibot.app.agent_registry import AgentRegistry
from minibot.app.event_bus import EventBus
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.scheduler_service import ScheduledPromptService
from minibot.core.memory import KeyValueMemory, MemoryBackend
from minibot.llm.provider_factory import LLMClient
from minibot.adapters.config.loader import load_settings
from minibot.adapters.config.schema import Settings, TelegramChannelConfig
from minibot.adapters.logging.setup import configure_logging
from minibot.adapters.memory.kv_sqlalchemy import SQLAlchemyKeyValueMemory
from minibot.adapters.memory.sqlalchemy import SQLAlchemyMemoryBackend
from minibot.adapters.scheduler.sqlalchemy_prompt_store import SQLAlchemyScheduledPromptStore


class AppContainer:
    _settings: Optional[Settings] = None
    _logger: Optional[logging.Logger] = None
    _event_bus: Optional[EventBus] = None
    _memory_backend: Optional[MemoryBackend] = None
    _kv_memory_backend: Optional[KeyValueMemory] = None
    _llm_client: Optional[LLMClient] = None
    _llm_factory: Optional[LLMClientFactory] = None
    _agent_registry: Optional[AgentRegistry] = None
    _prompt_store: Optional[SQLAlchemyScheduledPromptStore] = None
    _prompt_service: Optional[ScheduledPromptService] = None

    @classmethod
    def configure(cls, config_path: Path | None = None) -> None:
        cls._settings = load_settings(config_path)
        cls._settings.logging.log_level = cls._settings.runtime.log_level
        cls._logger = configure_logging(cls._settings.logging)
        cls._event_bus = EventBus()
        cls._memory_backend = SQLAlchemyMemoryBackend(cls._settings.memory)
        if cls._settings.tools.kv_memory.enabled:
            cls._kv_memory_backend = SQLAlchemyKeyValueMemory(cls._settings.tools.kv_memory)
        else:
            cls._kv_memory_backend = None
        cls._llm_factory = LLMClientFactory(cls._settings)
        cls._llm_client = cls._llm_factory.create_default()
        if cls._settings.agents.enabled:
            cls._agent_registry = AgentRegistry(load_agent_specs(cls._settings.agents.directory))
        else:
            cls._agent_registry = AgentRegistry([])
        prompts_config = cls._settings.scheduler.prompts
        if prompts_config.enabled:
            cls._prompt_store = SQLAlchemyScheduledPromptStore(prompts_config)
            cls._prompt_service = ScheduledPromptService(
                repository=cls._prompt_store,
                event_bus=cls._event_bus,
                config=prompts_config,
            )
        else:
            cls._prompt_store = None
            cls._prompt_service = None

    @classmethod
    def get_settings(cls) -> Settings:
        if cls._settings is None:
            raise RuntimeError("container not configured")
        return cls._settings

    @classmethod
    def get_logger(cls) -> logging.Logger:
        if cls._logger is None:
            raise RuntimeError("container not configured")
        return cls._logger

    @classmethod
    def get_event_bus(cls) -> EventBus:
        if cls._event_bus is None:
            raise RuntimeError("container not configured")
        return cls._event_bus

    @classmethod
    def get_memory_backend(cls) -> MemoryBackend:
        if cls._memory_backend is None:
            raise RuntimeError("memory backend not configured")
        return cls._memory_backend

    @classmethod
    def get_kv_memory_backend(cls) -> KeyValueMemory | None:
        return cls._kv_memory_backend

    @classmethod
    def get_llm_client(cls) -> LLMClient:
        if cls._llm_client is None:
            raise RuntimeError("LLM client not configured")
        return cls._llm_client

    @classmethod
    def get_llm_factory(cls) -> LLMClientFactory:
        if cls._llm_factory is None:
            raise RuntimeError("LLM factory not configured")
        return cls._llm_factory

    @classmethod
    def get_agent_registry(cls) -> AgentRegistry:
        if cls._agent_registry is None:
            raise RuntimeError("agent registry not configured")
        return cls._agent_registry

    @classmethod
    def get_scheduled_prompt_service(cls) -> ScheduledPromptService | None:
        return cls._prompt_service

    @classmethod
    def get_telegram_config(cls) -> TelegramChannelConfig:
        return cls.get_settings().channels.get("telegram")  # type: ignore[return-value]

    @classmethod
    async def initialize_storage(cls) -> None:
        await cls._initialize_backend(cls.get_memory_backend())
        if cls._kv_memory_backend is not None:
            await cls._initialize_backend(cls._kv_memory_backend)
        if cls._prompt_store is not None:
            await cls._initialize_backend(cls._prompt_store)

    @classmethod
    async def _initialize_backend(cls, backend: object) -> None:
        init = getattr(backend, "initialize", None)
        if callable(init):
            result = init()
            if inspect.isawaitable(result):
                await result
