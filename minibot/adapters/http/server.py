from __future__ import annotations

import asyncio
import base64
import hmac
import importlib.metadata
import logging
import os
import platform
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.types import Scope
from starlette.websockets import WebSocket

from minibot.adapters.config.schema import STATIC_CACHE_DISABLED_ENVIRONMENTS, HTTPServerConfig

type RouteSpec = tuple[str, Callable[[Request], Awaitable[Any]], tuple[str, ...]]
type WebSocketSpec = tuple[str, Callable[[WebSocket], Awaitable[None]]]

HEALTH_PATH = "/health"
STATIC_PATH = "/static"

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Overwritten by set_nav_entries() at boot; the default keeps templates renderable without it.
_templates.env.globals["nav_entries"] = []

try:
    _VERSION = importlib.metadata.version("minibot")
except importlib.metadata.PackageNotFoundError:
    _VERSION = "unknown"


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


class _NoCacheStaticFiles(StaticFiles):
    def file_response(
        self,
        full_path: str | Path,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-store"
        return response


def render(request: Request, template_name: str, context: dict[str, Any] | None = None) -> Any:
    """Render a template from this package's directory. Public so extensions serving a page do
    not reach into the module-private ``Jinja2Templates``."""
    return _templates.TemplateResponse(request, template_name, context or {})


def set_nav_entries(entries: Sequence[tuple[str, str]]) -> None:
    """Publish the navigation menu as a Jinja global, once, at daemon boot.

    A global rather than per-handler context: the menu is the same on every page, and an
    extension's own handler should not have to know it exists in order to render inside it.
    """
    _templates.env.globals["nav_entries"] = list(entries)


@dataclass(frozen=True)
class DashboardData:
    """What the ``/`` dashboard needs, gathered at daemon boot (mostly static; ``pending_turns``
    is a live lookup since that count changes while the process runs)."""

    extensions: Sequence[Mapping[str, Any]]
    tool_names: Sequence[str]
    routes: Sequence[RouteSpec]
    websockets: Sequence[WebSocketSpec]
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


def _describe_routes(routes: Sequence[RouteSpec], websockets: Sequence[WebSocketSpec]) -> list[dict[str, str]]:
    described = [
        {"method": "GET", "path": "/", "note": ""},
        {"method": "GET", "path": HEALTH_PATH, "note": "public"},
        {"method": "GET", "path": f"{STATIC_PATH}/*", "note": ""},
    ]
    described.extend(
        {"method": method, "path": path, "note": ""} for path, _handler, methods in routes for method in methods
    )
    described.extend({"method": "WS", "path": path, "note": ""} for path, _handler in websockets)
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
            "routes": _describe_routes(data.routes, data.websockets),
        }
        return _templates.TemplateResponse(request, "dashboard.html", context)

    return ("/", _dashboard, ("GET",))


HISTORY_PATH = "/history"
_HISTORY_MESSAGE_LIMIT = 200


def build_history_route(memory: Any) -> RouteSpec:
    """Build the ``/history`` route: sessions index, or one session's messages with ``?session=``.

    The session id goes in a query parameter rather than the path because ids embed a colon
    (``telegram:12345``, see ``minibot.shared.utils.session_identifier``).
    """

    async def _history(request: Request) -> Any:
        session_id = request.query_params.get("session")
        if not session_id:
            sessions = await memory.list_sessions()
            return _templates.TemplateResponse(request, "history.html", {"sessions": sessions})
        entries = list(await memory.get_history(session_id, limit=_HISTORY_MESSAGE_LIMIT))
        context = {"session_id": session_id, "entries": entries, "limit": _HISTORY_MESSAGE_LIMIT}
        return _templates.TemplateResponse(request, "history_detail.html", context)

    return (HISTORY_PATH, _history, ("GET",))


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


_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class _BasicAuth:
    """ASGI middleware: every route except ``/health`` needs the configured HTTP Basic credentials.

    Sends ``WWW-Authenticate`` so browsers prompt natively, unlike the bearer token scheme. Also
    refuses cross-site state-changing requests: browsers cache Basic credentials per origin and
    re-attach them automatically, even to a plain ``<form>`` a malicious page submits -- unlike
    bearer auth, there is no custom header standing in the way. ``Sec-Fetch-Site`` is set by the
    browser itself, not by page script, so a ``cross-site`` value is trustworthy here; a missing
    header (non-browser clients) is let through unchanged.
    """

    def __init__(self, app: Any, user: str, password: str) -> None:
        self._app = app
        self._expected = base64.b64encode(f"{user}:{password}".encode())

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if scope["method"] in _UNSAFE_METHODS and dict(scope["headers"]).get(b"sec-fetch-site") == b"cross-site":
            await JSONResponse({"error": "cross-site request rejected"}, status_code=403)(scope, receive, send)
            return
        if scope["path"] == HEALTH_PATH or self._authorized(scope):
            await self._app(scope, receive, send)
            return
        headers = {"WWW-Authenticate": 'Basic realm="minibot"'}
        response = JSONResponse({"error": "unauthorized"}, status_code=401, headers=headers)
        await response(scope, receive, send)

    def _authorized(self, scope: Any) -> bool:
        header = dict(scope["headers"]).get(b"authorization", b"")
        prefix = b"Basic "
        return header.startswith(prefix) and hmac.compare_digest(header[len(prefix) :], self._expected)


class _SecurityHeaders:
    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        async def send_with_headers(message: Any) -> None:
            if scope["type"] == "http" and message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = (
                    "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; media-src 'self'; "
                    "style-src 'self'; script-src 'self' 'unsafe-eval'; object-src 'none'; base-uri 'self'; "
                    "frame-ancestors 'none'"
                )
            await send(message)

        await self._app(scope, receive, send_with_headers)


class HttpServer:
    """Serves ``routes`` next to the daemon, on the daemon's own event loop."""

    def __init__(
        self,
        config: HTTPServerConfig,
        routes: Sequence[RouteSpec] = (),
        websockets: Sequence[WebSocketSpec] = (),
        *,
        environment: str = "production",
    ) -> None:
        self._config = config
        self._routes = list(routes)
        self._websockets = list(websockets)
        self._disable_static_cache = environment.casefold() in STATIC_CACHE_DISABLED_ENVIRONMENTS
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
        static_files: StaticFiles
        if self._disable_static_cache:
            static_files = _NoCacheStaticFiles(directory=STATIC_DIR)
        else:
            static_files = StaticFiles(directory=STATIC_DIR)
        routes = [
            Route(HEALTH_PATH, _health),
            Mount(STATIC_PATH, app=static_files, name="static"),
            *(Route(path, handler, methods=list(methods)) for path, handler, methods in self._routes),
            *(WebSocketRoute(path, handler) for path, handler in self._websockets),
        ]
        app: Any = _SecurityHeaders(Starlette(routes=routes))
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
