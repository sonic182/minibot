from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ...app.event_bus import EventBus
from ...core.memory import MemoryBackend
from ...llm.provider_factory import LLMClient
from ..config.loader import load_settings
from ..config.schema import Settings, TelegramChannelConfig
from ..logging.setup import configure_logging
from ..memory.sqlalchemy import SQLAlchemyMemoryBackend


class AppContainer:
    _settings: Optional[Settings] = None
    _logger: Optional[logging.Logger] = None
    _event_bus: Optional[EventBus] = None
    _memory_backend: Optional[MemoryBackend] = None
    _llm_client: Optional[LLMClient] = None

    @classmethod
    def configure(cls, config_path: Path | None = None) -> None:
        cls._settings = load_settings(config_path)
        cls._logger = configure_logging(cls._settings.logging)
        cls._event_bus = EventBus()
        cls._memory_backend = SQLAlchemyMemoryBackend(cls._settings.memory)
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
    def get_llm_client(cls) -> LLMClient:
        if cls._llm_client is None:
            raise RuntimeError("LLM client not configured")
        return cls._llm_client

    @classmethod
    def get_telegram_config(cls) -> TelegramChannelConfig:
        return cls.get_settings().channels.get("telegram")  # type: ignore[return-value]

    @classmethod
    async def initialize_storage(cls) -> None:
        backend = cls.get_memory_backend()
        init = getattr(backend, "initialize", None)
        if callable(init):
            await init()
