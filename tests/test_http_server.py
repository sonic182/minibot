from __future__ import annotations

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
async def test_no_token_configured_leaves_routes_open() -> None:
    instance = HttpServer(HTTPServerConfig(enabled=True, host="127.0.0.1", port=0), [("/ping", _pong, ("GET",))])
    # Captured off the logger itself, not caplog: configure_logging() turns propagation off for the
    # "minibot" tree, so whether root sees anything depends on which tests ran before this one.
    with _captured_warnings("minibot.http") as records:
        await instance.start()
    try:
        assert any("auth_token is not configured" in record.getMessage() for record in records)
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
