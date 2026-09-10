from __future__ import annotations

import logging

from minibot.adapters.config.schema import RagToolConfig
from minibot.app.extensions import ExtensionContext
from minibot.core.vectors import VectorStore

from ..tools._storage import managed_storage

_logger = logging.getLogger("minibot.rag")


class _RagService:
    def __init__(self, config: RagToolConfig, store: VectorStore) -> None:
        self._config = config
        self._store = store

    async def start(self) -> None:
        vector_size = (
            self._config.embedding.truncate_dim
            if self._config.embedding.truncate_dim is not None
            else self._config.embedding.dim
        )
        await self._store.ensure_collection(self._config.collection_name, vector_size)
        for field in ("document_id", "user_id", "agent_id", "chat_id", "filename", "tags", "categories"):
            await self._store.create_payload_index(self._config.collection_name, field, field_schema="keyword")

    async def stop(self) -> None:
        return None


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tools.rag.enabled:
        return
    config = mb.settings.tools.rag
    if config.chunk_overlap_tokens >= config.chunk_size_tokens:
        raise ValueError("tools.rag.chunk_overlap_tokens must be less than tools.rag.chunk_size_tokens")
    if config.chunk_size_tokens > config.embedding.max_sequence_tokens:
        raise ValueError("tools.rag.chunk_size_tokens must not exceed tools.rag.embedding.max_sequence_tokens")

    from minibot.llm.tools.rag_tools import RagTools

    storage = managed_storage(mb.settings, error_message="tools.rag.enabled requires tools.file_storage.enabled")

    _warn_on_probable_qdrant_deployment(config)

    store: VectorStore
    if config.backend == "qdrant":
        from minibot.adapters.qdrant.client import AsyncQdrantClient

        store = AsyncQdrantClient(url=config.qdrant_url)
    else:
        from minibot.adapters.vectors.sqlite import SqliteVectorStore

        store = SqliteVectorStore(config)

    mb.add_tool(RagTools(config=config, store=store, storage=storage).bindings())
    mb.add_service(_RagService(config, store))


def _warn_on_probable_qdrant_deployment(config: RagToolConfig) -> None:
    """Catch the silent half of the backend default flip.

    A config written before ``backend`` existed carries a customized ``qdrant_url`` and no
    ``backend`` key, so it now resolves to the SQLite default and starts against an empty local
    store. Nothing fails -- the tools register and ``rag_search`` just returns no results -- which is
    the worst way to find out an indexed corpus went missing.
    """
    if config.backend != "sqlite":
        return
    default_qdrant_url = RagToolConfig.model_fields["qdrant_url"].default
    if config.qdrant_url == default_qdrant_url:
        return
    _logger.warning(
        "tools.rag.backend is 'sqlite' (the default) but tools.rag.qdrant_url is customized; "
        'if this deployment indexed documents into Qdrant, set tools.rag.backend = "qdrant" -- '
        "RAG is otherwise reading an empty local store",
        extra={"qdrant_url": config.qdrant_url, "sqlite_url": config.sqlite_url},
    )
