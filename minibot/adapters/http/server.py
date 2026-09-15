from __future__ import annotations

import asyncio
import base64
import hmac
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from minibot.adapters.config.schema import HTTPServerConfig

type RouteSpec = tuple[str, Callable[[Request], Awaitable[Any]], tuple[str, ...]]

HEALTH_PATH = "/health"


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


async def _homepage(_: Request) -> HTMLResponse:
    return HTMLResponse("<title>minibot</title><p>minibot is running.</p>")


class _BearerAuth:
    """ASGI middleware: every route except ``/health`` needs the configured token.

    Health stays open so a container healthcheck does not need the credential.
    """

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._token = token

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] == HEALTH_PATH or self._authorized(scope):
            await self._app(scope, receive, send)
            return
        await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)

    def _authorized(self, scope: Any) -> bool:
        header = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        prefix = "Bearer "
        return header.startswith(prefix) and hmac.compare_digest(header[len(prefix) :], self._token)


class _BasicAuth:
    """ASGI middleware: every route except ``/health`` needs the configured HTTP Basic credentials.

    Sends ``WWW-Authenticate`` so browsers prompt natively, unlike the bearer token scheme.
    """

    def __init__(self, app: Any, user: str, password: str) -> None:
        self._app = app
        self._expected = base64.b64encode(f"{user}:{password}".encode()).decode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] == HEALTH_PATH or self._authorized(scope):
            await self._app(scope, receive, send)
            return
        headers = {"WWW-Authenticate": 'Basic realm="minibot"'}
        response = JSONResponse({"error": "unauthorized"}, status_code=401, headers=headers)
        await response(scope, receive, send)

    def _authorized(self, scope: Any) -> bool:
        header = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        prefix = "Basic "
        return header.startswith(prefix) and hmac.compare_digest(header[len(prefix) :], self._expected)


class HttpServer:
    """Serves ``routes`` next to the daemon, on the daemon's own event loop."""

    def __init__(self, config: HTTPServerConfig, routes: Sequence[RouteSpec] = ()) -> None:
        self._config = config
        self._routes = list(routes)
        self._logger = logging.getLogger("minibot.http")
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def port(self) -> int:
        """The port actually bound, which differs from the configured one when that was 0."""
        for server in getattr(self._server, "servers", ()):
            for socket in server.sockets:
                return int(socket.getsockname()[1])
        return self._config.port

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        routes = [
            Route(HEALTH_PATH, _health),
            Route("/", _homepage),
            *(Route(path, handler, methods=list(methods)) for path, handler, methods in self._routes),
        ]
        app: Any = Starlette(routes=routes)
        if self._config.basic_auth_user and self._config.basic_auth_password:
            app = _BasicAuth(app, self._config.basic_auth_user, self._config.basic_auth_password)
        elif self._config.auth_token:
            app = _BearerAuth(app, self._config.auth_token)
        else:
            # Allowed on loopback (the schema rejects it anywhere else), but never silently: the
            # next route someone registers is served to anything that can reach this port.
            self._logger.warning(
                "[http] auth_token is not configured, every route is served without authentication",
                extra={"component": "http", "host": self._config.host, "routes": len(self._routes)},
            )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=self._config.host,
                port=self._config.port,
                log_config=None,
                lifespan="off",
                access_log=False,
            )
        )
        # The daemon already owns SIGINT/SIGTERM; uvicorn installing its own would hijack shutdown.
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._server = server
        self._task = asyncio.create_task(server.serve())
        # ponytail: poll for readiness, uvicorn exposes no started-event; swap if it ever grows one.
        while not server.started and not self._task.done():
            await asyncio.sleep(0.01)
        if self._task.done():
            # A bind failure lives in the task, so awaiting it turns a silent dead server into a boot crash.
            await self._task
        self._logger.info(
            "http server listening",
            extra={"component": "http", "host": self._config.host, "port": self.port, "routes": len(self._routes)},
        )

    async def stop(self) -> None:
        if self._server is None or self._task is None:
            return
        self._server.should_exit = True
        await self._task
        self._server = None
        self._task = None
