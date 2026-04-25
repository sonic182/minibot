from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from llm_async.models import Tool

from minibot.adapters.config.schema import RagToolConfig
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.qdrant.client import AsyncQdrantClient
from minibot.llm.tools.arg_utils import int_with_default, optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_integer, nullable_string, strict_object
from minibot.rag.chunking import count_text_tokens, truncate_text_tokens
from minibot.rag.document_ingestion import extract_indexable_document
from minibot.rag.retrieval import delete_document, index_document, list_metadata_facets, retrieve_context

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
            ToolBinding(tool=self._list_metadata_schema(), handler=self._handle_list_metadata),
            ToolBinding(tool=self._delete_schema(), handler=self._handle_delete),
        ]

    def _index_schema(self) -> Tool:
        return Tool(
            name="rag_index",
            description=load_tool_description("rag_index"),
            parameters=strict_object(
                properties={
                    "file_path": {"type": "string", "description": "Path to the text or PDF file to index."},
                    "document_id": nullable_string("Stable identifier for this document. Auto-generated if omitted."),
                    "user_id": nullable_string("Optional user scope tag."),
                    "agent_id": nullable_string("Optional agent scope tag."),
                    "chat_id": nullable_string("Optional chat scope tag."),
                    "tags": _nullable_string_list_schema("Optional tags stored with each chunk."),
                    "categories": _nullable_string_list_schema("Optional categories stored with each chunk."),
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
                    "chat_id": nullable_string("Restrict results to this chat scope."),
                    "filename": nullable_string("Restrict results to chunks from this exact filename."),
                    "tags": _nullable_string_list_schema("Restrict results to matching tags."),
                    "categories": _nullable_string_list_schema("Restrict results to matching categories."),
                    "limit": nullable_integer(minimum=1, description="Number of results to return."),
                },
                required=["query"],
            ),
        )

    async def _handle_index(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        file_path_raw = require_non_empty_str(payload, "file_path")
        file_path = self._resolve_path(file_path_raw)
        document = await asyncio.to_thread(extract_indexable_document, file_path)

        document_id = optional_str(payload.get("document_id")) or _hash_path(file_path_raw)

        chunks = await index_document(
            client=self._qdrant,
            collection=self._config.collection_name,
            document_id=document_id,
            text=document.text,
            filename=file_path.name,
            source_type=document.source_type,
            mime_type=document.mime_type,
            user_id=_scope_value(payload.get("user_id"), context.user_id, field="user_id"),
            agent_id=_agent_scope_value(payload.get("agent_id"), context.owner_id),
            chat_id=_scope_value(payload.get("chat_id"), context.chat_id, field="chat_id"),
            tags=_normalize_string_list(payload.get("tags"), field="tags"),
            categories=_normalize_string_list(payload.get("categories"), field="categories"),
            chunk_size_tokens=self._config.chunk_size_tokens,
            chunk_overlap_tokens=self._config.chunk_overlap_tokens,
            embedding_model=self._config.embedding.model,
            truncate_dim=self._config.embedding.truncate_dim,
        )

        _logger.info("rag indexed", extra={"document_id": document_id, "chunks": chunks})
        return {"document_id": document_id, "chunks_indexed": chunks}

    def _list_metadata_schema(self) -> Tool:
        return Tool(
            name="rag_list_metadata",
            description=load_tool_description("rag_list_metadata"),
            parameters=strict_object(
                properties={
                    "document_id": nullable_string("Restrict metadata discovery to this document."),
                    "user_id": nullable_string("Restrict metadata discovery to this user scope."),
                    "agent_id": nullable_string("Restrict metadata discovery to this agent scope."),
                    "chat_id": nullable_string("Restrict metadata discovery to this chat scope."),
                    "limit": nullable_integer(minimum=1, description="Maximum number of facet values to return."),
                },
                required=[],
            ),
        )

    def _delete_schema(self) -> Tool:
        return Tool(
            name="rag_delete",
            description=load_tool_description("rag_delete"),
            parameters=strict_object(
                properties={
                    "document_id": nullable_string("Delete all chunks for this document."),
                    "user_id": nullable_string("Delete all chunks tagged with this user scope."),
                    "agent_id": nullable_string("Delete all chunks tagged with this agent scope."),
                    "chat_id": nullable_string("Delete all chunks tagged with this chat scope."),
                    "tags": _nullable_string_list_schema("Delete all chunks tagged with any of these tags."),
                    "categories": _nullable_string_list_schema(
                        "Delete all chunks tagged with any of these categories."
                    ),
                },
                required=[],
            ),
        )

    async def _handle_delete(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        document_id = optional_str(payload.get("document_id"))
        explicit_user_id = optional_str(payload.get("user_id"))
        explicit_agent_id = optional_str(payload.get("agent_id"))
        explicit_chat_id = optional_str(payload.get("chat_id"))
        tags = _normalize_string_list(payload.get("tags"), field="tags")
        categories = _normalize_string_list(payload.get("categories"), field="categories")

        if not any((document_id, explicit_user_id, explicit_agent_id, explicit_chat_id, tags, categories)):
            raise ValueError("rag_delete requires at least one explicit filter")

        await delete_document(
            client=self._qdrant,
            collection=self._config.collection_name,
            document_id=document_id,
            user_id=_scope_value(explicit_user_id, context.user_id, field="user_id"),
            agent_id=_agent_scope_value(explicit_agent_id, context.owner_id),
            chat_id=_scope_value(explicit_chat_id, context.chat_id, field="chat_id"),
            tags=tags,
            categories=categories,
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
            user_id=_scope_value(payload.get("user_id"), context.user_id, field="user_id"),
            agent_id=_agent_scope_value(payload.get("agent_id"), context.owner_id),
            chat_id=_scope_value(payload.get("chat_id"), context.chat_id, field="chat_id"),
            filename=optional_str(payload.get("filename")),
            tags=_normalize_string_list(payload.get("tags"), field="tags"),
            categories=_normalize_string_list(payload.get("categories"), field="categories"),
            embedding_model=self._config.embedding.model,
            truncate_dim=self._config.embedding.truncate_dim,
            rerank_enabled=self._config.rerank.enabled,
            rerank_model=self._config.rerank.model,
            rerank_candidate_limit=self._config.rerank.candidate_limit,
            rerank_max_results=self._config.rerank.max_results,
        )

        if self._config.truncate_result_tokens:
            results, truncated, truncated_tokens = await asyncio.to_thread(
                _truncate_search_results,
                results,
                max_tokens=self._config.max_result_tokens,
                embedding_model=self._config.embedding.model,
                truncate_dim=self._config.embedding.truncate_dim,
            )
            if truncated:
                _logger.info(
                    "rag search results truncated",
                    extra={
                        "query_length": len(query),
                        "max_result_tokens": self._config.max_result_tokens,
                        "truncated_tokens": truncated_tokens,
                        "returned_results": len(results),
                    },
                )
            return {
                "results": results,
                "truncated": truncated,
                "truncated_tokens": truncated_tokens,
            }

        return {"results": results, "truncated": False, "truncated_tokens": 0}

    async def _handle_list_metadata(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = int_with_default(
            payload.get("limit"),
            default=self._config.search_limit,
            field="limit",
            min_value=1,
        )
        facets = await list_metadata_facets(
            client=self._qdrant,
            collection=self._config.collection_name,
            limit=limit,
            document_id=optional_str(payload.get("document_id")),
            user_id=_scope_value(payload.get("user_id"), context.user_id, field="user_id"),
            agent_id=_agent_scope_value(payload.get("agent_id"), context.owner_id),
            chat_id=_scope_value(payload.get("chat_id"), context.chat_id, field="chat_id"),
        )
        return facets

    def _resolve_path(self, raw: str):
        if self._storage is None:
            raise ValueError("rag_index requires tools.file_storage.enabled = true")
        return self._storage.resolve_existing_file(raw)


def _hash_path(path: str) -> str:
    return "doc_" + hashlib.sha1(path.encode()).hexdigest()[:16]  # noqa: S324


def _scope_value(raw_value: Any, context_value: int | None, *, field: str) -> str | None:
    value = optional_str(raw_value)
    if context_value is None:
        return value

    context_resolved = str(context_value)
    if value is None:
        return context_resolved
    if value != context_resolved:
        raise ValueError(f"{field} must match the current runtime context")
    return context_resolved


def _agent_scope_value(raw_value: Any, owner_id: str | None) -> str | None:
    value = optional_str(raw_value)
    if owner_id is None:
        return value
    if value is None:
        return owner_id
    if value != owner_id:
        raise ValueError("agent_id must match the current runtime context")
    return owner_id


def _normalize_string_list(raw_value: Any, *, field: str) -> list[str] | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, list):
        raise ValueError(f"{field} must be a list of strings")

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        if not isinstance(item, str):
            raise ValueError(f"{field} must be a list of strings")
        candidate = item.strip().lower()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized or None


def _nullable_string_list_schema(description: str) -> dict[str, Any]:
    return {
        "type": ["array", "null"],
        "description": description,
        "items": {"type": "string"},
    }


def _truncate_search_results(
    results: list[dict[str, Any]],
    *,
    max_tokens: int,
    embedding_model: str,
    truncate_dim: int | None,
) -> tuple[list[dict[str, Any]], bool, int]:
    total_tokens = sum(
        count_text_tokens(
            str(item.get("text", "")),
            embedding_model=embedding_model,
            truncate_dim=truncate_dim,
        )
        for item in results
    )
    if total_tokens <= max_tokens:
        return results, False, 0

    remaining = max_tokens
    truncated_results: list[dict[str, Any]] = []
    for item in results:
        if remaining <= 0:
            break

        text = str(item.get("text", ""))
        text_tokens = count_text_tokens(text, embedding_model=embedding_model, truncate_dim=truncate_dim)
        if text_tokens <= remaining:
            truncated_results.append(item)
            remaining -= text_tokens
            continue

        kept, omitted = truncate_text_tokens(
            text,
            max_tokens=remaining,
            embedding_model=embedding_model,
            truncate_dim=truncate_dim,
        )
        truncated_results.append(
            {
                **item,
                "text": f"{kept}\n...[truncated {omitted} tokens]",
            }
        )
        remaining = 0

    return truncated_results, True, total_tokens - max_tokens
