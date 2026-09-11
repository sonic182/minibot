"""Contracts for persistent relation graphs."""

from __future__ import annotations

from typing import Any, Protocol


class GraphStore(Protocol):
    """Store typed relations scoped to a graph namespace and owner."""

    async def link(
        self,
        *,
        graph: str,
        owner_id: str,
        source: str,
        rel: str,
        target: str,
        attrs: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def unlink(self, *, graph: str, owner_id: str, source: str, rel: str, target: str) -> dict[str, Any]: ...

    async def neighbors(
        self,
        *,
        graph: str,
        owner_id: str,
        node: str,
        direction: str = "out",
        depth: int = 1,
        rel: str | None = None,
        history: bool = False,
        limit: int = 100,
        max_nodes: int = 100,
    ) -> dict[str, Any]: ...

    async def path(
        self,
        *,
        graph: str,
        owner_id: str,
        source: str,
        target: str,
        max_depth: int = 4,
    ) -> dict[str, Any]: ...

    async def search(
        self,
        *,
        graph: str,
        owner_id: str,
        query: str,
        limit: int = 25,
        history: bool = False,
    ) -> dict[str, Any]: ...

    async def merge(self, *, graph: str, owner_id: str, source: str, target: str) -> dict[str, Any]: ...

    async def close(self) -> None: ...
