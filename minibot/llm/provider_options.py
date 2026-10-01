from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderOption:
    """A provider an agent or a runtime delegation override may target."""

    name: str
    api_format: str
    base_url: str | None
    models: tuple[str, ...]

    def as_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "api_format": self.api_format,
            "base_url": self.base_url,
            "models": list(self.models),
        }


def find_provider(name: str, options: Sequence[ProviderOption]) -> ProviderOption | None:
    normalized = name.strip().lower()
    return next((option for option in options if option.name == normalized), None)
