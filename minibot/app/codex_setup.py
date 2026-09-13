from __future__ import annotations

from pathlib import Path
from typing import Any

from minibot.llm.services.codex_setup import (
    CodexCredentialsError,
    CodexDependencyError,
    CodexLoginError,
    list_model_slugs,
    load_credentials,
    login,
    resolve_auth_path,
)


def resolve_codex_auth_path(auth_path: str | None) -> Path:
    """Resolve the path used by the Codex setup workflow."""
    return resolve_auth_path(auth_path)


def load_codex_credentials(auth_path: Path) -> Any:
    """Load credentials for an already authenticated Codex account."""
    return load_credentials(auth_path)


async def login_to_codex(*, device_code: bool, auth_path: Path, verbose: bool = False) -> Any:
    """Authenticate a Codex account through the LLM integration."""
    return await login(device_code=device_code, auth_path=auth_path, verbose=verbose)


async def list_codex_model_slugs(credentials: Any) -> list[str]:
    """List models exposed by the authenticated Codex account."""
    return await list_model_slugs(credentials)


__all__ = [
    "CodexCredentialsError",
    "CodexDependencyError",
    "CodexLoginError",
    "list_codex_model_slugs",
    "load_codex_credentials",
    "login_to_codex",
    "resolve_codex_auth_path",
]
