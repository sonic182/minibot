from __future__ import annotations

from pathlib import Path
from typing import Any


class CodexSetupError(RuntimeError):
    """Base error for optional Codex setup operations."""


class CodexDependencyError(CodexSetupError):
    """Raised when the optional Codex package is unavailable."""


class CodexCredentialsError(CodexSetupError):
    """Raised when Codex credentials cannot be loaded."""


class CodexLoginError(CodexSetupError):
    """Raised when the Codex login flow fails."""


def resolve_auth_path(auth_path: str | None) -> Path:
    """Resolve the configured Codex credentials path."""
    if auth_path:
        return Path(auth_path).expanduser()
    return Path.home() / ".minibot" / "auth_codex.json"


def load_credentials(auth_path: Path) -> Any:
    """Load OAuth credentials from ``auth_path``."""
    try:
        from llm_async_codex import CodexAuthError
        from llm_async_codex import load_credentials as load_codex_credentials
    except ImportError as exc:
        raise CodexDependencyError(_dependency_message()) from exc
    try:
        return load_codex_credentials(auth_path)
    except CodexAuthError as exc:
        raise CodexCredentialsError(str(exc)) from exc


async def login(*, device_code: bool, auth_path: Path, verbose: bool = False) -> Any:
    """Run the Codex OAuth login flow."""
    try:
        from llm_async_codex import CodexLoginError as ProviderCodexLoginError
        from llm_async_codex import login as login_to_codex
    except ImportError as exc:
        raise CodexDependencyError(_dependency_message()) from exc
    try:
        return await login_to_codex(device_code=device_code, auth_path=auth_path, verbose=verbose)
    except ProviderCodexLoginError as exc:
        raise CodexLoginError(str(exc)) from exc


async def list_model_slugs(credentials: Any) -> list[str]:
    """List models available to the authenticated Codex account."""
    provider = _provider(credentials)
    try:
        return await provider.list_model_slugs()
    finally:
        await provider.client.connector.cleanup()


async def get_model_capabilities(auth_path: Path, model_name: str) -> Any:
    """Return capabilities for a model available to the authenticated account."""
    provider = _provider(load_credentials(auth_path))
    try:
        return await provider.get_model_capabilities(model_name)
    finally:
        await provider.client.connector.cleanup()


def _provider(credentials: Any) -> Any:
    try:
        from minibot.llm.providers.codex import PatchedCodexProvider
    except ImportError as exc:
        raise CodexDependencyError(_dependency_message()) from exc
    return PatchedCodexProvider(credentials)


def _dependency_message() -> str:
    return (
        "llm-async-codex is required; install with `poetry install --extras codex` or `poetry install --all-extras`."
    )
