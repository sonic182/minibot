from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from minibot.app.event_bus import EventBus
from minibot.core.memory import KeyValueMemory, MemoryBackend
from minibot.llm.provider_factory import LLMClient
from minibot.adapters.config.loader import load_settings
from minibot.adapters.config.schema import Settings, TelegramChannelConfig
from minibot.adapters.logging.setup import configure_logging
from minibot.adapters.memory.kv_sqlalchemy import SQLAlchemyKeyValueMemory
from minibot.adapters.memory.sqlalchemy import SQLAlchemyMemoryBackend


class AppContainer:
    _settings: Optional[Settings] = None
    _logger: Optional[logging.Logger] = None
    _event_bus: Optional[EventBus] = None
    _memory_backend: Optional[MemoryBackend] = None
    _kv_memory_backend: Optional[KeyValueMemory] = None
    _llm_client: Optional[LLMClient] = None

    @classmethod
    def configure(cls, config_path: Path | None = None) -> None:
        cls._settings = load_settings(config_path)
        cls._logger = configure_logging(cls._settings.logging)
        cls._event_bus = EventBus()
        cls._memory_backend = SQLAlchemyMemoryBackend(cls._settings.memory)
        if cls._settings.kv_memory.enabled:
            cls._kv_memory_backend = SQLAlchemyKeyValueMemory(cls._settings.kv_memory)
        else:
            cls._kv_memory_backend = None
        cls._llm_client = LLMClient(cls._settings.llm)

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
    def get_telegram_config(cls) -> TelegramChannelConfig:
        return cls.get_settings().channels.get("telegram")  # type: ignore[return-value]

    @classmethod
    async def initialize_storage(cls) -> None:
        await cls._initialize_backend(cls.get_memory_backend())
        if cls._kv_memory_backend is not None:
            await cls._initialize_backend(cls._kv_memory_backend)

    @classmethod
    async def _initialize_backend(cls, backend: object) -> None:
        init = getattr(backend, "initialize", None)
        if callable(init):
            await init()
