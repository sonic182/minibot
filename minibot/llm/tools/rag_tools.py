from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from llm_async.models import Tool

from minibot.adapters.config.schema import RagToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.qdrant.client import AsyncQdrantClient
from minibot.llm.tools.arg_utils import int_with_default, optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_integer, nullable_string, strict_object
from minibot.rag.retrieval import delete_document, index_document, retrieve_context

_logger = logging.getLogger("minibot.rag_tools")


class RagTools:
    def __init__(
        self,
        config: RagToolConfig,
        qdrant: AsyncQdrantClient,
        storage: LocalFileStorage | None = None,
    ) -> None:
        self._config = config
        self._qdrant = qdrant
        self._storage = storage

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._index_schema(), handler=self._handle_index),
            ToolBinding(tool=self._search_schema(), handler=self._handle_search),
            ToolBinding(tool=self._delete_schema(), handler=self._handle_delete),
        ]

    def _index_schema(self) -> Tool:
        return Tool(
            name="rag_index",
            description=load_tool_description("rag_index"),
            parameters=strict_object(
                properties={
                    "file_path": {"type": "string", "description": "Path to the text file to index."},
                    "document_id": nullable_string("Stable identifier for this document. Auto-generated if omitted."),
                    "source_name": nullable_string("Human-readable label stored with each chunk."),
                    "user_id": nullable_string("Optional user scope tag."),
                    "agent_id": nullable_string("Optional agent scope tag."),
                    "chat_id": nullable_string("Optional chat scope tag."),
                },
                required=["file_path"],
            ),
        )

    def _search_schema(self) -> Tool:
        return Tool(
            name="rag_search",
            description=load_tool_description("rag_search"),
            parameters=strict_object(
                properties={
                    "query": {"type": "string", "description": "Natural language search query."},
                    "document_id": nullable_string("Restrict results to this document."),
                    "user_id": nullable_string("Restrict results to this user scope."),
                    "agent_id": nullable_string("Restrict results to this agent scope."),
                    "limit": nullable_integer(minimum=1, description="Number of results to return."),
                },
                required=["query"],
            ),
        )

    async def _handle_index(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        file_path_raw = require_non_empty_str(payload, "file_path")
        file_path = self._resolve_path(file_path_raw)
        text = file_path.read_text(encoding="utf-8", errors="replace")

        document_id = optional_str(payload.get("document_id")) or _hash_path(file_path_raw)
        source_name = optional_str(payload.get("source_name")) or file_path.name

        chunks = await index_document(
            client=self._qdrant,
            collection=self._config.collection_name,
            document_id=document_id,
            text=text,
            source_name=source_name,
            user_id=_scope_str(payload.get("user_id"), context.user_id),
            agent_id=optional_str(payload.get("agent_id")) or context.owner_id,
            chat_id=_scope_str(payload.get("chat_id"), context.chat_id),
            chunk_size=self._config.chunk_size,
            chunk_overlap=self._config.chunk_overlap,
            embedding_model=self._config.embedding.model,
            truncate_dim=self._config.embedding.truncate_dim,
        )

        _logger.info("rag indexed", extra={"document_id": document_id, "chunks": chunks})
        return {"document_id": document_id, "chunks_indexed": chunks}

    def _delete_schema(self) -> Tool:
        return Tool(
            name="rag_delete",
            description=load_tool_description("rag_delete"),
            parameters=strict_object(
                properties={
                    "document_id": nullable_string("Delete all chunks for this document."),
                    "user_id": nullable_string("Delete all chunks tagged with this user scope."),
                    "agent_id": nullable_string("Delete all chunks tagged with this agent scope."),
                },
                required=[],
            ),
        )

    async def _handle_delete(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        await delete_document(
            client=self._qdrant,
            collection=self._config.collection_name,
            document_id=optional_str(payload.get("document_id")),
            user_id=_scope_str(payload.get("user_id"), context.user_id),
            agent_id=optional_str(payload.get("agent_id")) or context.owner_id,
        )
        return {"deleted": True}

    async def _handle_search(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = require_non_empty_str(payload, "query")
        limit = int_with_default(
            payload.get("limit"),
            default=self._config.search_limit,
            field="limit",
            min_value=1,
        )

        results = await retrieve_context(
            client=self._qdrant,
            collection=self._config.collection_name,
            query=query,
            limit=limit,
            document_id=optional_str(payload.get("document_id")),
            user_id=_scope_str(payload.get("user_id"), context.user_id),
            agent_id=optional_str(payload.get("agent_id")) or context.owner_id,
            embedding_model=self._config.embedding.model,
            truncate_dim=self._config.embedding.truncate_dim,
        )

        return {"results": results}

    def _resolve_path(self, raw: str) -> Path:
        if self._storage is not None:
            return self._storage.resolve_existing_file(raw)
        path = Path(raw)
        if not path.exists():
            raise ValueError(f"file not found: {raw}")
        return path


def _hash_path(path: str) -> str:
    return "doc_" + hashlib.sha1(path.encode()).hexdigest()[:16]  # noqa: S324


def _scope_str(payload_value: Any, context_value: int | str | None) -> str | None:
    return optional_str(payload_value) or (str(context_value) if context_value is not None else None)
