from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import stat
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from llm_async.models import Response
from llm_async_codex import (
    CodexAuthError,
    CodexCredentials,
    CodexLoginError,
    CodexProvider,
    load_credentials,
    refresh_credentials,
    save_credentials,
)
from llm_async_codex.oauth import TOKEN_REFRESH_SAFETY_MARGIN_SECONDS

from minibot.config.schema import CodexConfig
from minibot.llm.providers.openai_responses import PatchedOpenAIResponsesProvider

_LOCK_POLL_SECONDS = 0.05
_logger = logging.getLogger("minibot.codex_provider")


class PatchedCodexProvider(CodexProvider, PatchedOpenAIResponsesProvider):
    """Codex subscriptions reject stream=False; minibot's pipeline only calls the plain,
    non-streaming ``acomplete`` contract. Force streaming and drain it here so callers get a
    fully-populated ``Response`` like every other provider.

    Base order keeps MiniBot's native tool formatting while retaining Codex-specific behaviour.

    The model list is requested with ``models_client_version`` (``[codex] version``) rather than the
    version ``llm-async-codex`` hardcodes, because the endpoint hides models newer than the client version.

    The auth file is the shared source of truth for credentials. Several providers (main client, safety
    classifier, forked task workers, the startup model lookup) read the same file, and every refresh rotates
    the refresh token, so a refresh runs under a file lock, re-reads the file first and adopts credentials
    another provider already refreshed instead of spending a refresh token that was rotated away.

    The lock is a polled non-blocking ``flock`` so a cancelled waiter leaves no thread behind holding it. The
    refreshed file is written to a temporary sibling and renamed so readers outside the lock never see it
    half-written; if the rename fails (for example on a bind-mounted single file) it is written in place.
    Once the server has answered a refresh, its tokens are always kept: the refresh is finished even when the
    caller is cancelled, because the old refresh token is already revoked. ``auth_path`` is required so no
    provider can refresh outside the lock or fall back to the Codex CLI's own auth file."""

    _stream_responses_request = PatchedOpenAIResponsesProvider._stream_responses_request

    def __init__(
        self,
        credentials: CodexCredentials,
        *,
        auth_path: Path,
        models_client_version: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(credentials, auth_path=auth_path, **kwargs)
        self._models_client_version = models_client_version or CodexConfig().version

    async def _ensure_fresh_credentials(self) -> None:
        if not _needs_refresh(self.credentials):
            return
        async with self._refresh_lock:
            if not _needs_refresh(self.credentials):
                return
            async with _auth_file_lock(self.auth_path):
                stored = _load_stored_credentials(self.auth_path)
                cancelled = False
                if _needs_refresh(stored):
                    stored, cancelled = await _finish_despite_cancellation(
                        asyncio.ensure_future(_refresh_atomically(stored, self.auth_path))
                    )
                self.credentials = stored
                self.api_key = stored.access_token
            if cancelled:
                raise asyncio.CancelledError

    async def _ensure_models_cache(self) -> list[dict[str, Any]]:
        await self._ensure_fresh_credentials()
        if self._models_cache is None:
            payload = await self.request("GET", f"/models?client_version={self._models_client_version}")
            models = payload.get("models") if isinstance(payload, dict) else None
            entries = models if isinstance(models, list) else []
            self._models_cache = [entry for entry in entries if isinstance(entry, dict)]
        return self._models_cache

    async def acomplete(self, *args: Any, **kwargs: Any) -> Response:
        kwargs["stream"] = True
        response = await super().acomplete(*args, **kwargs)
        if response.stream_generator is not None:
            async for _ in response.stream_generator:
                pass
        return response


def _needs_refresh(credentials: CodexCredentials) -> bool:
    if not credentials.refresh_token or credentials.expires_at is None:
        return False
    return time.time() >= credentials.expires_at - TOKEN_REFRESH_SAFETY_MARGIN_SECONDS


def _load_stored_credentials(auth_path: Path) -> CodexCredentials:
    try:
        return load_credentials(auth_path)
    except CodexAuthError as exc:
        raise CodexLoginError(f"{exc}; run `minibot codex login` to log in again") from exc


async def _finish_despite_cancellation(
    refresh: asyncio.Future[CodexCredentials],
) -> tuple[CodexCredentials, bool]:
    cancelled = False
    while True:
        try:
            return await asyncio.shield(refresh), cancelled
        except asyncio.CancelledError:
            if refresh.cancelled():
                raise
            cancelled = True


@contextlib.asynccontextmanager
async def _auth_file_lock(auth_path: Path) -> AsyncIterator[None]:
    lock_path = auth_path.with_name(f"{auth_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


async def _refresh_atomically(credentials: CodexCredentials, auth_path: Path) -> CodexCredentials:
    handle, temp_name = tempfile.mkstemp(dir=auth_path.parent, prefix=f".{auth_path.name}.", suffix=".tmp")
    os.close(handle)
    try:
        refreshed = await refresh_credentials(credentials, Path(temp_name))
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
    try:
        _replace_durably(Path(temp_name), auth_path)
    except OSError:
        _logger.warning("could not replace the Codex auth file atomically; writing it in place", exc_info=True)
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        try:
            save_credentials(refreshed, auth_path)
        except OSError:
            _logger.error(
                "could not save refreshed Codex credentials; other clients will need a new login", exc_info=True
            )
    return refreshed


def _replace_durably(temp_path: Path, auth_path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.chmod(temp_path, stat.S_IMODE(os.stat(auth_path).st_mode))
    with open(temp_path, "rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temp_path, auth_path)
    directory = os.open(auth_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
