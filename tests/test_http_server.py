from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from contextlib import contextmanager

import aiosonic
import pytest
import pytest_asyncio
from pydantic import ValidationError
from starlette.responses import JSONResponse

from minibot.adapters.config.schema import HTTPServerConfig
from minibot.adapters.http import HttpServer
from minibot.adapters.http.server import _BasicAuth, _BearerAuth

TOKEN = "s3cret"


async def _pong(_request) -> JSONResponse:
    return JSONResponse({"pong": True})


@contextmanager
def _captured_warnings(name: str) -> Iterator[list[logging.LogRecord]]:
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger(name)
    previous_level = logger.level
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


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
async def test_extension_route_accepts_basic_auth() -> None:
    instance = HttpServer(
        HTTPServerConfig(
            enabled=True,
            host="127.0.0.1",
            port=0,
            basic_auth_user="user",
            basic_auth_password="password",
        ),
        [("/ping", _pong, ("GET",))],
    )
    await instance.start()
    try:
        async with aiosonic.HTTPClient() as client:
            url = f"http://127.0.0.1:{instance.port}/ping"
            unauthorized = await client.get(url)
            assert unauthorized.status_code == 401
            assert unauthorized.headers.get("www-authenticate") == 'Basic realm="minibot"'
            credentials = base64.b64encode(b"user:password").decode()
            assert (await client.get(url, headers={"Authorization": f"Basic {credentials}"})).status_code == 200
    finally:
        await instance.stop()


@pytest.mark.parametrize(
    ("middleware", "header"),
    [
        (_BearerAuth(None, TOKEN), b"Bearer \xff"),
        (_BasicAuth(None, "user", "password"), b"Basic \xff"),
    ],
)
def test_malformed_authorization_header_is_rejected(middleware: _BearerAuth | _BasicAuth, header: bytes) -> None:
    assert not middleware._authorized({"headers": [(b"authorization", header)]})


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_releases_the_port() -> None:
    instance = HttpServer(HTTPServerConfig(enabled=True, host="127.0.0.1", port=0))
    await instance.start()
    port = instance.port
    await instance.stop()
    await instance.stop()

    reused = HttpServer(HTTPServerConfig(enabled=True, host="127.0.0.1", port=port))
    await reused.start()
    try:
        assert reused.port == port
    finally:
        await reused.stop()


@pytest.mark.asyncio
async def test_no_token_configured_leaves_routes_open() -> None:
    instance = HttpServer(HTTPServerConfig(enabled=True, host="127.0.0.1", port=0), [("/ping", _pong, ("GET",))])
    with _captured_warnings("minibot.http") as records:
        await instance.start()
    try:
        assert any("auth_token is not configured" in record.getMessage() for record in records)
        async with aiosonic.HTTPClient() as client:
            response = await client.get(f"http://127.0.0.1:{instance.port}/ping")
            assert response.status_code == 200
    finally:
        await instance.stop()


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost"])
def test_non_literal_loopback_bind_requires_a_token(host: str) -> None:
    with pytest.raises(ValidationError, match="auth_token or basic_auth_user/basic_auth_password is required"):
        HTTPServerConfig(enabled=True, host=host, port=8080)

    HTTPServerConfig(enabled=True, host=host, port=8080, auth_token=TOKEN)
    HTTPServerConfig(enabled=False, host=host, port=8080)
