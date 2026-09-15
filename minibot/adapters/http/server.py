from __future__ import annotations

import asyncio
import base64
import hmac
import importlib.metadata
import logging
import platform
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from minibot.adapters.config.schema import HTTPServerConfig

type RouteSpec = tuple[str, Callable[[Request], Awaitable[Any]], tuple[str, ...]]

HEALTH_PATH = "/health"
STATIC_PATH = "/static"

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

try:
    _VERSION = importlib.metadata.version("minibot")
except importlib.metadata.PackageNotFoundError:
    _VERSION = "unknown"


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@dataclass(frozen=True)
class DashboardData:
    """What the ``/`` dashboard needs, gathered at daemon boot (mostly static; ``pending_turns``
    is a live lookup since that count changes while the process runs)."""

    extensions: Sequence[Mapping[str, Any]]
    tool_names: Sequence[str]
    routes: Sequence[RouteSpec]
    started_at: datetime
    llm_provider: str
    llm_model: str
    telegram_enabled: bool
    pending_turns: Callable[[], Awaitable[int]]


def _format_uptime(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


_CONTRIBUTIONS = (("tools", "tool"), ("services", "service"), ("subscriptions", "subscription"), ("routes", "route"))


def _describe_extensions(summaries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Turn contribution counts into display rows. Nothing contributed means the module loaded
    but its ``register()`` bailed out — disabled in config, not broken."""
    described = []
    for summary in summaries:
        parts = [
            f"{summary[key]} {noun}{'' if summary[key] == 1 else 's'}"
            for key, noun in _CONTRIBUTIONS
            if summary.get(key)
        ]
        described.append({"name": summary["name"], "detail": " · ".join(parts) or "inactive", "active": bool(parts)})
    return described


def _describe_routes(routes: Sequence[RouteSpec]) -> list[dict[str, str]]:
    described = [
        {"method": "GET", "path": "/", "note": ""},
        {"method": "GET", "path": HEALTH_PATH, "note": "public"},
        {"method": "GET", "path": f"{STATIC_PATH}/*", "note": ""},
    ]
    described.extend(
        {"method": method, "path": path, "note": ""} for path, _handler, methods in routes for method in methods
    )
    return sorted(described, key=lambda route: route["path"])


def build_dashboard_route(data: DashboardData) -> RouteSpec:
    """Build the ``/`` route: a read-only status page for this MiniBot instance."""

    async def _dashboard(request: Request) -> Any:
        context = {
            "version": _VERSION,
            "python_version": platform.python_version(),
            "uptime": _format_uptime(datetime.now(UTC) - data.started_at),
            "llm_provider": data.llm_provider,
            "llm_model": data.llm_model,
            "telegram_enabled": data.telegram_enabled,
            "pending_turns": await data.pending_turns(),
            "extensions": _describe_extensions(data.extensions),
            "tool_names": list(data.tool_names),
            "routes": _describe_routes(data.routes),
        }
        return _templates.TemplateResponse(request, "dashboard.html", context)

    return ("/", _dashboard, ("GET",))


class _BearerAuth:
    """ASGI middleware: every route except ``/health`` needs the configured token.

    Health stays open so a container healthcheck does not need the credential.
    """

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] == HEALTH_PATH or self._authorized(scope):
            await self._app(scope, receive, send)
            return
        await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)

    def _authorized(self, scope: Any) -> bool:
        header = dict(scope["headers"]).get(b"authorization", b"")
        prefix = b"Bearer "
        return header.startswith(prefix) and hmac.compare_digest(header[len(prefix) :], self._token)


class _BasicAuth:
    """ASGI middleware: every route except ``/health`` needs the configured HTTP Basic credentials.

    Sends ``WWW-Authenticate`` so browsers prompt natively, unlike the bearer token scheme.
    """

    def __init__(self, app: Any, user: str, password: str) -> None:
        self._app = app
        self._expected = base64.b64encode(f"{user}:{password}".encode())

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] == HEALTH_PATH or self._authorized(scope):
            await self._app(scope, receive, send)
            return
        headers = {"WWW-Authenticate": 'Basic realm="minibot"'}
        response = JSONResponse({"error": "unauthorized"}, status_code=401, headers=headers)
        await response(scope, receive, send)

    def _authorized(self, scope: Any) -> bool:
        header = dict(scope["headers"]).get(b"authorization", b"")
        prefix = b"Basic "
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
            Mount(STATIC_PATH, app=StaticFiles(directory=STATIC_DIR), name="static"),
            *(Route(path, handler, methods=list(methods)) for path, handler, methods in self._routes),
        ]
        app: Any = Starlette(routes=routes)
        if self._config.basic_auth_user and self._config.basic_auth_password:
            app = _BasicAuth(app, self._config.basic_auth_user, self._config.basic_auth_password)
        elif self._config.auth_token:
            app = _BearerAuth(app, self._config.auth_token)
        else:
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
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._server = server
        self._task = asyncio.create_task(server.serve())
        # ponytail: poll for readiness, uvicorn exposes no started-event; swap if it ever grows one.
        while not server.started and not self._task.done():
            await asyncio.sleep(0.01)
        if self._task.done():
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
