from __future__ import annotations

from minibot.adapters.config.schema import RagToolConfig
from minibot.adapters.qdrant.client import AsyncQdrantClient
from minibot.app.extensions import ExtensionContext

from ..tools._storage import managed_storage


class _RagService:
    def __init__(self, config: RagToolConfig, qdrant: AsyncQdrantClient) -> None:
        self._config = config
        self._qdrant = qdrant

    async def start(self) -> None:
        vector_size = (
            self._config.embedding.truncate_dim
            if self._config.embedding.truncate_dim is not None
            else self._config.embedding.dim
        )
        await self._qdrant.ensure_collection(self._config.collection_name, vector_size)
        for field in ("document_id", "user_id", "agent_id", "chat_id", "filename", "tags", "categories"):
            await self._qdrant.create_payload_index(self._config.collection_name, field, field_schema="keyword")

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
    qdrant = AsyncQdrantClient(url=config.qdrant_url)
    mb.add_tool(RagTools(config=config, qdrant=qdrant, storage=storage).bindings())
    mb.add_service(_RagService(config, qdrant))
