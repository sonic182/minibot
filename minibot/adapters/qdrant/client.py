from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import aiosonic


class AsyncQdrantClient:
    def __init__(self, url: str) -> None:
        self._url = url.rstrip("/")
        self._client = aiosonic.HTTPClient()

    async def ensure_collection(self, collection_name: str, vector_size: int) -> None:
        encoded_collection = _encode_path_segment(collection_name)
        response = await self._request("GET", f"/collections/{encoded_collection}", allow_not_found=True)
        if response is not None:
            existing_vector_size = _extract_vector_size(response)
            if existing_vector_size != vector_size:
                raise ValueError(
                    f"qdrant collection '{collection_name}' has vector size {existing_vector_size}, "
                    f"expected {vector_size}"
                )
            return

        await self._request(
            "PUT",
            f"/collections/{encoded_collection}",
            payload={"vectors": {"size": vector_size, "distance": "Cosine"}},
        )

    async def create_payload_index(self, collection_name: str, field_name: str, field_schema: str = "keyword") -> None:
        encoded_collection = _encode_path_segment(collection_name)
        await self._request(
            "PUT",
            f"/collections/{encoded_collection}/index?wait=true",
            payload={"field_name": field_name, "field_schema": field_schema},
        )

    async def upsert_points(self, collection_name: str, points: list[dict[str, Any]]) -> None:
        encoded_collection = _encode_path_segment(collection_name)
        await self._request(
            "PUT",
            f"/collections/{encoded_collection}/points?wait=true",
            payload={"points": points},
        )

    async def delete_by_filter(self, collection_name: str, filters: dict[str, Any]) -> None:
        encoded_collection = _encode_path_segment(collection_name)
        await self._request(
            "POST",
            f"/collections/{encoded_collection}/points/delete?wait=true",
            payload={"filter": filters},
        )

    async def search(
        self,
        collection_name: str,
        vector: list[float],
        *,
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        encoded_collection = _encode_path_segment(collection_name)
        payload: dict[str, Any] = {
            "vector": vector,
            "limit": limit,
            "with_payload": True,
        }
        if filters:
            payload["filter"] = filters

        response = await self._request("POST", f"/collections/{encoded_collection}/points/search", payload=payload)
        result = response.get("result", [])
        if not isinstance(result, list):
            raise RuntimeError("qdrant search returned an unexpected response")
        return result

    async def facet(
        self,
        collection_name: str,
        *,
        key: str,
        limit: int,
        filters: dict[str, Any] | None = None,
        exact: bool = False,
    ) -> list[dict[str, Any]]:
        encoded_collection = _encode_path_segment(collection_name)
        payload: dict[str, Any] = {"key": key, "limit": limit, "exact": exact}
        if filters:
            payload["filter"] = filters

        response = await self._request("POST", f"/collections/{encoded_collection}/facet", payload=payload)
        result = response.get("result", {})
        hits = result.get("hits", [])
        if not isinstance(hits, list):
            raise RuntimeError("qdrant facet returned an unexpected response")
        return hits

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        kwargs: dict[str, Any] = {"headers": {"Accept": "application/json"}}
        if payload is not None:
            kwargs["headers"]["Content-Type"] = "application/json"
            kwargs["data"] = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        response = await self._client.request(f"{self._url}{path}", method=method, **kwargs)
        body = await response.content()
        if allow_not_found and response.status_code == 404:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            message = body.decode("utf-8", errors="replace")
            raise RuntimeError(f"qdrant request failed: {response.status_code} {message}")
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))


def _encode_path_segment(value: str) -> str:
    return quote(value, safe="")


def _extract_vector_size(response: dict[str, Any]) -> int:
    config = response.get("result", {}).get("config", {})
    params = config.get("params", {})
    vectors = params.get("vectors")
    if isinstance(vectors, dict):
        size = vectors.get("size")
        if isinstance(size, int):
            return size
    raise RuntimeError("qdrant collection returned an unexpected vector config")
