from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("llm_async_codex")

from llm_async_codex import CodexCredentials, CodexLoginError, load_credentials, save_credentials  # noqa: E402
from llm_async_codex import oauth as codex_oauth  # noqa: E402

from minibot.llm.providers.codex import PatchedCodexProvider  # noqa: E402


class _RotatingTokenServer:
    """OAuth token endpoint that rotates the refresh token and rejects one already used, like the real one."""

    def __init__(self) -> None:
        self.calls = 0
        self._current = "rt-0"

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        if refresh_token != self._current:
            raise CodexLoginError("token refresh failed: 401")
        self.calls += 1
        self._current = f"rt-{self.calls}"
        return {"access_token": f"at-{self.calls}", "refresh_token": self._current, "expires_in": 3600}


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> _RotatingTokenServer:
    fake = _RotatingTokenServer()
    monkeypatch.setattr(codex_oauth, "refresh_access_token", fake.refresh)
    return fake


@pytest.fixture
def auth_path(tmp_path: Path) -> Path:
    path = tmp_path / "auth_codex.json"
    save_credentials(CodexCredentials("at-0", "rt-0", "acct", expires_at=0.0), path)
    return path


def _provider(auth_path: Path) -> PatchedCodexProvider:
    return PatchedCodexProvider(load_credentials(auth_path), auth_path=auth_path)


@pytest.mark.asyncio
async def test_second_provider_adopts_rotated_credentials_instead_of_reusing_stale_token(
    server: _RotatingTokenServer, auth_path: Path
) -> None:
    main, classifier = _provider(auth_path), _provider(auth_path)

    await main._ensure_fresh_credentials()
    await classifier._ensure_fresh_credentials()

    assert server.calls == 1
    assert classifier.credentials == main.credentials
    assert classifier.api_key == "at-1"


@pytest.mark.asyncio
async def test_concurrent_refreshes_across_providers_hit_the_server_once(
    server: _RotatingTokenServer, auth_path: Path
) -> None:
    providers = [_provider(auth_path) for _ in range(3)]

    await asyncio.gather(*(provider._ensure_fresh_credentials() for provider in providers))

    assert server.calls == 1
    stored = load_credentials(auth_path)
    assert all(provider.credentials == stored for provider in providers)
    assert stored.refresh_token == "rt-1"
    assert list(auth_path.parent.glob("*.tmp")) == []
