from __future__ import annotations

import aiosonic
import pytest
import pytest_asyncio
from pydantic import ValidationError
from starlette.responses import JSONResponse

from minibot.adapters.config.schema import HTTPServerConfig
from minibot.adapters.http import HttpServer

TOKEN = "s3cret"


async def _pong(_request) -> JSONResponse:
    return JSONResponse({"pong": True})


@pytest_asyncio.fixture()
async def server():
    instance = HttpServer(
        HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, auth_token=TOKEN),
        [("/ping", _pong, ("GET",))],
    )
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.mark.asyncio
async def test_health_needs_no_token(server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        response = await client.get(f"http://127.0.0.1:{server.port}/health")
        assert response.status_code == 200
        assert await response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_extension_route_requires_the_token(server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        url = f"http://127.0.0.1:{server.port}/ping"
        assert (await client.get(url)).status_code == 401
        assert (await client.get(url, headers={"Authorization": "Bearer wrong"})).status_code == 401
        authorized = await client.get(url, headers={"Authorization": f"Bearer {TOKEN}"})
        assert authorized.status_code == 200
        assert await authorized.json() == {"pong": True}


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_releases_the_port() -> None:
    instance = HttpServer(HTTPServerConfig(enabled=True, host="127.0.0.1", port=0))
    await instance.start()
    port = instance.port
    await instance.stop()
    await instance.stop()

    # The port is free again, so a fresh server can claim it.
    reused = HttpServer(HTTPServerConfig(enabled=True, host="127.0.0.1", port=port))
    await reused.start()
    try:
        assert reused.port == port
    finally:
        await reused.stop()


@pytest.mark.asyncio
async def test_no_token_configured_leaves_routes_open(caplog: pytest.LogCaptureFixture) -> None:
    instance = HttpServer(HTTPServerConfig(enabled=True, host="127.0.0.1", port=0), [("/ping", _pong, ("GET",))])
    with caplog.at_level("WARNING", logger="minibot.http"):
        await instance.start()
    try:
        assert "auth_token is not configured" in caplog.text
        async with aiosonic.HTTPClient() as client:
            response = await client.get(f"http://127.0.0.1:{instance.port}/ping")
            assert response.status_code == 200
    finally:
        await instance.stop()


def test_non_loopback_bind_requires_a_token() -> None:
    with pytest.raises(ValidationError, match="auth_token is required"):
        HTTPServerConfig(enabled=True, host="0.0.0.0", port=8080)

    HTTPServerConfig(enabled=True, host="0.0.0.0", port=8080, auth_token=TOKEN)
    HTTPServerConfig(enabled=False, host="0.0.0.0", port=8080)
