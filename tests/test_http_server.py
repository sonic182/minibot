from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiosonic
import pytest
import pytest_asyncio
from pydantic import ValidationError
from starlette.responses import JSONResponse

from minibot.adapters.config.schema import HTTPServerConfig
from minibot.adapters.http import (
    DashboardData,
    HttpServer,
    build_dashboard_route,
    build_history_route,
    set_nav_entries,
)
from minibot.adapters.http.server import _BasicAuth, _BearerAuth
from tests.fixtures.memory import InMemoryMemoryStore

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


async def _no_pending_turns() -> int:
    return 0


@pytest.mark.asyncio
async def test_build_http_server_populates_dashboard_data(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.adapters import http as http_module
    from minibot.app import daemon as daemon_module

    pending_turn_store = SimpleNamespace(list_pending=AsyncMock(return_value=[("pending-turn", "{}")]))
    extensions = SimpleNamespace(
        routes=[("/ping", _pong, ("GET",))],
        pages=Mock(return_value=[]),
        summaries=Mock(
            return_value=[{"name": "scheduler", "tools": 1, "services": 0, "subscriptions": 0, "routes": 0}]
        ),
    )
    build_dashboard_route = Mock(return_value=("/", _pong, ("GET",)))

    settings = SimpleNamespace(
        http=HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, auth_token=TOKEN),
        providers={},
        llm=SimpleNamespace(
            provider="openai_responses",
            base_url="https://opencode.ai/zen/go/v1",
            model="minimax",
        ),
        runtime=SimpleNamespace(environment="development"),
        channels=SimpleNamespace(telegram=SimpleNamespace(enabled=True)),
        tools=SimpleNamespace(file_storage=SimpleNamespace(enabled=False)),
    )
    dispatcher = SimpleNamespace(main_agent_tool_names=["web_search"])

    monkeypatch.setattr(daemon_module.AppContainer, "get_pending_turn_store", lambda: pending_turn_store)
    monkeypatch.setattr(daemon_module.AppContainer, "get_memory_backend", InMemoryMemoryStore)
    monkeypatch.setattr(http_module, "build_dashboard_route", build_dashboard_route)

    daemon_module._build_http_server(
        settings,
        dispatcher,
        extensions,
        datetime.now(UTC),
        logging.getLogger(__name__),
    )

    dashboard_data = build_dashboard_route.call_args.args[0]
    assert dashboard_data.llm_provider == "opencode-go"
    assert dashboard_data.tool_names == ["web_search"]
    assert dashboard_data.extensions == [
        {"name": "scheduler", "tools": 1, "services": 0, "subscriptions": 0, "routes": 0}
    ]
    assert await dashboard_data.pending_turns() == 1


@pytest_asyncio.fixture()
async def dashboard_server():
    data = DashboardData(
        extensions=[
            {"name": "scheduler", "tools": 2, "services": 1, "subscriptions": 0, "routes": 0},
            {"name": "rabbitmq", "tools": 0, "services": 0, "subscriptions": 0, "routes": 0},
        ],
        tool_names=["send_message", "web_search"],
        routes=[("/ping", _pong, ("GET",))],
        started_at=datetime.now(UTC),
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        telegram_enabled=True,
        pending_turns=_no_pending_turns,
    )
    route = build_dashboard_route(data)
    instance = HttpServer(HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, auth_token=TOKEN), [route])
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.mark.asyncio
async def test_dashboard_lists_enabled_extensions_and_tools(dashboard_server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        response = await client.get(
            f"http://127.0.0.1:{dashboard_server.port}/", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 200
        body = await response.text()
        assert "scheduler" in body
        assert "send_message" in body
        assert "web_search" in body


@pytest.mark.asyncio
async def test_dashboard_marks_extensions_that_contributed_nothing(dashboard_server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        response = await client.get(
            f"http://127.0.0.1:{dashboard_server.port}/", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        body = await response.text()
        # scheduler registered things, rabbitmq bailed out of register() -- loaded but idle.
        assert "2 tools &middot; 1 service" in body or "2 tools · 1 service" in body
        assert "inactive" in body


@pytest.mark.asyncio
async def test_dashboard_shows_status_and_routes(dashboard_server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        response = await client.get(
            f"http://127.0.0.1:{dashboard_server.port}/", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        body = await response.text()
        assert "openai / gpt-4o-mini" in body
        assert "0 pending turns" in body
        assert "Telegram" in body
        assert "/ping" in body
        assert "/health" in body


@pytest.mark.asyncio
async def test_dashboard_requires_the_token(dashboard_server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        response = await client.get(f"http://127.0.0.1:{dashboard_server.port}/")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_static_css_is_served(dashboard_server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        response = await client.get(
            f"http://127.0.0.1:{dashboard_server.port}/static/dashboard.css",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "cache_control"),
    [("debug", "no-store"), ("development", "no-store"), ("production", None)],
)
async def test_static_assets_disable_cache_only_in_development(environment: str, cache_control: str | None) -> None:
    instance = HttpServer(
        HTTPServerConfig(enabled=True, host="127.0.0.1", port=0),
        environment=environment,
    )
    await instance.start()
    try:
        async with aiosonic.HTTPClient() as client:
            response = await client.get(f"http://127.0.0.1:{instance.port}/static/dashboard.css")
            assert response.headers.get("Cache-Control") == cache_control
    finally:
        await instance.stop()


@pytest_asyncio.fixture()
async def history_server():
    memory = InMemoryMemoryStore()
    await memory.append_history("telegram:42", "user", "hola minibot")
    await memory.append_history("telegram:42", "assistant", "hola, en que ayudo")
    instance = HttpServer(
        HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, auth_token=TOKEN),
        [build_history_route(memory)],
    )
    await instance.start()
    try:
        yield instance
    finally:
        await instance.stop()


@pytest.mark.asyncio
async def test_history_lists_sessions(history_server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        response = await client.get(
            f"http://127.0.0.1:{history_server.port}/history", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 200
        body = await response.text()
        assert "telegram:42" in body
        # The index shows counts, not message bodies.
        assert "hola minibot" not in body


@pytest.mark.asyncio
async def test_history_shows_one_session(history_server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        response = await client.get(
            f"http://127.0.0.1:{history_server.port}/history?session=telegram:42",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert response.status_code == 200
        body = await response.text()
        assert "hola minibot" in body
        assert "hola, en que ayudo" in body


@pytest.mark.asyncio
async def test_history_requires_the_token(history_server: HttpServer) -> None:
    async with aiosonic.HTTPClient() as client:
        assert (await client.get(f"http://127.0.0.1:{history_server.port}/history")).status_code == 401


@pytest.mark.asyncio
async def test_nav_entries_render_in_the_menu(dashboard_server: HttpServer) -> None:
    set_nav_entries([("/", "Home"), ("/memory", "Memory")])
    try:
        async with aiosonic.HTTPClient() as client:
            response = await client.get(
                f"http://127.0.0.1:{dashboard_server.port}/", headers={"Authorization": f"Bearer {TOKEN}"}
            )
            body = await response.text()
            assert 'href="/memory"' in body
            # Nothing registered a graph page, so it must not show up.
            assert 'href="/graph"' not in body
    finally:
        set_nav_entries([])


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


@pytest.mark.asyncio
async def test_basic_auth_rejects_a_cross_site_post() -> None:
    # A browser auto-attaches cached Basic credentials to any request to this origin, including
    # one a malicious page triggers via a plain <form> post; Sec-Fetch-Site: cross-site is the
    # browser-set signal that tells the two apart from a same-origin form submission.
    instance = HttpServer(
        HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, basic_auth_user="user", basic_auth_password="pw"),
        [("/ping", _pong, ("GET", "POST"))],
    )
    await instance.start()
    try:
        async with aiosonic.HTTPClient() as client:
            url = f"http://127.0.0.1:{instance.port}/ping"
            credentials = base64.b64encode(b"user:pw").decode()
            headers = {"Authorization": f"Basic {credentials}"}
            cross_site = await client.post(url, headers={**headers, "Sec-Fetch-Site": "cross-site"})
            assert cross_site.status_code == 403
            same_origin = await client.post(url, headers={**headers, "Sec-Fetch-Site": "same-origin"})
            assert same_origin.status_code == 200
            no_header = await client.post(url, headers=headers)
            assert no_header.status_code == 200
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
