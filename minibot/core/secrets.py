from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class SecretVault(Protocol):
    def names(self) -> list[str]: ...

    def as_mapping(self) -> Mapping[str, str]: ...

    def get(self, name: str) -> str: ...
