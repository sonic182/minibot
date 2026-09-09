from __future__ import annotations

import inspect
import logging
from pathlib import Path

from minibot.adapters.config.loader import load_settings
from minibot.adapters.config.schema import Settings
from minibot.adapters.logging.setup import configure_logging
from minibot.adapters.memory.pending_turns import PendingTurnStore
from minibot.adapters.memory.sqlalchemy import SQLAlchemyMemoryBackend
from minibot.app.agent_definitions_loader import load_agent_specs
from minibot.app.agent_registry import AgentRegistry
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionRegistry, load_extensions
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.skill_registry import SkillRegistry
from minibot.app.token_limits_autoconfig import apply_runtime_token_autoconfig_async
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient


class AppContainer:
    _settings: Settings | None = None
    _logger: logging.Logger | None = None
    _event_bus: EventBus | None = None
    _memory_backend: MemoryBackend | None = None
    _pending_turn_store: PendingTurnStore | None = None
    _llm_client: LLMClient | None = None
    _llm_factory: LLMClientFactory | None = None
    _agent_registry: AgentRegistry | None = None
    _skill_registry: SkillRegistry | None = None
    _extensions: ExtensionRegistry | None = None
    _token_autoconfig_applied: bool = False

    @classmethod
    def configure(cls, config_path: Path | None = None, *, entrypoint: str = "daemon") -> None:
        cls._settings = load_settings(config_path)
        cls._settings.logging.log_level = cls._settings.runtime.log_level
        cls._logger = configure_logging(cls._settings.logging)
        agent_specs = load_agent_specs(cls._settings.orchestration.directory)
        cls._event_bus = EventBus()
        cls._memory_backend = SQLAlchemyMemoryBackend(cls._settings.memory)
        cls._pending_turn_store = PendingTurnStore(cls._settings.memory)
        cls._llm_factory = LLMClientFactory(cls._settings)
        cls._llm_client = cls._llm_factory.create_default()
        cls._agent_registry = AgentRegistry(agent_specs)
        if cls._settings.tools.skills.enabled:
            skill_paths = list(cls._settings.tools.skills.paths) or None
            cls._skill_registry = SkillRegistry(paths=skill_paths)
        else:
            cls._skill_registry = SkillRegistry([])
        cls._token_autoconfig_applied = False
        # Last, so an extension's register() sees a fully built container even though the
        # context handed to it exposes only settings, the bus and a logger.
        cls._extensions = load_extensions(cls._settings, cls._event_bus, cls._logger, entrypoint)

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
    def get_pending_turn_store(cls) -> PendingTurnStore:
        if cls._pending_turn_store is None:
            raise RuntimeError("pending turn store not configured")
        return cls._pending_turn_store

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
    def get_skill_registry(cls) -> SkillRegistry:
        if cls._skill_registry is None:
            raise RuntimeError("container not configured")
        return cls._skill_registry

    @classmethod
    def get_scheduled_prompt_service(cls) -> None:
        """Compatibility placeholder; scheduled prompts are a bundled extension."""
        return None

    @classmethod
    def get_task_manager(cls) -> None:
        """Compatibility placeholder; task backends are bundled extensions."""
        return None

    @classmethod
    def get_task_store(cls) -> None:
        """Compatibility placeholder; task backends are bundled extensions."""
        return None

    @classmethod
    def get_task_producer(cls) -> None:
        """Compatibility placeholder; task backends are bundled extensions."""
        return None

    @classmethod
    def get_kv_memory_backend(cls) -> None:
        """Compatibility placeholder; KV memory is a bundled extension."""
        return None

    @classmethod
    def get_extensions(cls) -> ExtensionRegistry:
        if cls._extensions is None:
            raise RuntimeError("container not configured")
        return cls._extensions

    @classmethod
    async def initialize_storage(cls) -> None:
        await cls._apply_runtime_token_autoconfig_if_needed()
        await cls._initialize_backend(cls.get_memory_backend())
        await cls._initialize_backend(cls.get_pending_turn_store())

    @classmethod
    async def _apply_runtime_token_autoconfig_if_needed(cls) -> None:
        if cls._token_autoconfig_applied:
            return
        settings = cls.get_settings()
        logger = cls.get_logger()
        registry = cls.get_agent_registry()
        agent_specs = await apply_runtime_token_autoconfig_async(
            settings=settings,
            agent_specs=registry.all(),
            logger=logger,
        )
        cls._llm_factory = LLMClientFactory(settings)
        cls._llm_client = cls._llm_factory.create_default()
        cls._agent_registry = AgentRegistry(agent_specs)
        cls._token_autoconfig_applied = True

    @classmethod
    async def _initialize_backend(cls, backend: object) -> None:
        init = getattr(backend, "initialize", None)
        if callable(init):
            result = init()
            if inspect.isawaitable(result):
                await result
