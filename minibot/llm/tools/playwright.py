from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import html
import ipaddress
import logging
from pathlib import Path
import re
import time
from typing import Any, Callable
from urllib.parse import urlparse

from llm_async.models import Tool

from minibot.adapters.config.schema import PlaywrightToolConfig
from minibot.llm.tools.base import ToolBinding, ToolContext

_WAIT_UNTIL_VALUES = {"load", "domcontentloaded", "networkidle"}
_IMAGE_TYPES = {"png", "jpeg"}
_MAX_GOTO_TIMEOUT_SECONDS = 10
_DROP_BLOCK_TAGS = ("script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed")
_DROP_LAYOUT_TAGS = ("nav", "footer", "aside", "form")


@dataclass
class _BrowserSession:
    playwright: Any
    browser: Any
    context: Any
    page: Any
    browser_name: str
    last_used_monotonic: float
    processed_snapshot: _ProcessedSnapshot | None = None


@dataclass
class _ProcessedSnapshot:
    raw_html: str
    clean_html: str
    clean_text: str
    content_hash: str
    generated_monotonic: float


def _load_playwright() -> Callable[[], Any]:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright dependency is not installed. Install with: poetry install --extras playwright"
        ) from exc

    return async_playwright


class PlaywrightTool:
    def __init__(self, config: PlaywrightToolConfig) -> None:
        self._config = config
        self._logger = logging.getLogger("minibot.playwright")
        self._sessions: dict[str, _BrowserSession] = {}
        self._owner_locks: dict[str, asyncio.Lock] = {}

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._open_schema(), handler=self._handle_open),
            ToolBinding(tool=self._navigate_schema(), handler=self._handle_navigate),
            ToolBinding(tool=self._info_schema(), handler=self._handle_info),
            ToolBinding(tool=self._get_html_schema(), handler=self._handle_get_html),
            ToolBinding(tool=self._get_text_schema(), handler=self._handle_get_text),
            ToolBinding(tool=self._wait_for_schema(), handler=self._handle_wait_for),
            ToolBinding(tool=self._click_schema(), handler=self._handle_click),
            ToolBinding(tool=self._extract_schema(), handler=self._handle_extract),
            ToolBinding(tool=self._close_schema(), handler=self._handle_close),
            ToolBinding(tool=self._close_quick_schema(), handler=self._handle_close),
        ]

    async def _handle_open(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        await self._cleanup_expired_sessions()
        async with self._owner_lock(owner_id):
            requested_browser = self._coerce_browser(payload.get("browser"))
            url = self._coerce_url(payload.get("url"))
            wait_until = self._coerce_wait_until(payload.get("wait_until"))
            timeout_seconds = self._coerce_timeout(
                payload.get("timeout_seconds"),
                default=self._config.navigation_timeout_seconds,
                field="timeout_seconds",
            )
            timeout_seconds = min(timeout_seconds, _MAX_GOTO_TIMEOUT_SECONDS)
            await self._validate_url(url)

            existing = self._sessions.get(owner_id)
            if existing is None:
                existing = await self._create_session(requested_browser)
                self._sessions[owner_id] = existing
            elif existing.browser_name != requested_browser:
                await self._close_session(existing)
                existing = await self._create_session(requested_browser)
                self._sessions[owner_id] = existing

            goto_result = await self._goto_with_timeout_fallback(
                existing.page,
                url=url,
                wait_until=wait_until,
                timeout_seconds=timeout_seconds,
            )
            if not goto_result["ok"]:
                if goto_result.get("timed_out"):
                    if self._config.postprocess_outputs:
                        await self._refresh_processed_snapshot(existing)
                    title = await existing.page.title()
                    existing.last_used_monotonic = time.monotonic()
                    return {
                        "ok": True,
                        "url": existing.page.url,
                        "title": title,
                        "browser": existing.browser_name,
                        "navigation_timed_out": True,
                        "error": goto_result.get("error"),
                    }
                return goto_result
            if self._config.postprocess_outputs:
                await self._refresh_processed_snapshot(existing)
            title = await existing.page.title()
            existing.last_used_monotonic = time.monotonic()
            result = {
                "ok": True,
                "url": existing.page.url,
                "title": title,
                "browser": existing.browser_name,
            }
            if goto_result.get("wait_until_fallback"):
                result["wait_until_fallback"] = goto_result["wait_until_fallback"]
            return result

    async def _handle_click(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        await self._cleanup_expired_sessions()
        async with self._owner_lock(owner_id):
            selector = _coerce_non_empty_string(payload.get("selector"), "selector")
            timeout_seconds = self._coerce_timeout(
                payload.get("timeout_seconds"),
                default=self._config.action_timeout_seconds,
                field="timeout_seconds",
            )
            session = self._sessions.get(owner_id)
            if session is None:
                return _browser_not_open_result("browser_click")
            await session.page.click(selector, timeout=timeout_seconds * 1000)
            session.processed_snapshot = None
            session.last_used_monotonic = time.monotonic()
            return {
                "ok": True,
                "selector": selector,
                "url": session.page.url,
            }

    async def _handle_navigate(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        await self._cleanup_expired_sessions()
        async with self._owner_lock(owner_id):
            url = self._coerce_url(payload.get("url"))
            wait_until = self._coerce_wait_until(payload.get("wait_until"))
            timeout_seconds = self._coerce_timeout(
                payload.get("timeout_seconds"),
                default=self._config.navigation_timeout_seconds,
                field="timeout_seconds",
            )
            timeout_seconds = min(timeout_seconds, _MAX_GOTO_TIMEOUT_SECONDS)
            await self._validate_url(url)
            session = self._sessions.get(owner_id)
            if session is None:
                return _browser_not_open_result("browser_navigate")
            goto_result = await self._goto_with_timeout_fallback(
                session.page,
                url=url,
                wait_until=wait_until,
                timeout_seconds=timeout_seconds,
            )
            if not goto_result["ok"]:
                if goto_result.get("timed_out"):
                    if self._config.postprocess_outputs:
                        await self._refresh_processed_snapshot(session)
                    title = await session.page.title()
                    session.last_used_monotonic = time.monotonic()
                    return {
                        "ok": True,
                        "url": session.page.url,
                        "title": title,
                        "browser": session.browser_name,
                        "navigation_timed_out": True,
                        "error": goto_result.get("error"),
                    }
                return goto_result
            if self._config.postprocess_outputs:
                await self._refresh_processed_snapshot(session)
            title = await session.page.title()
            session.last_used_monotonic = time.monotonic()
            result = {
                "ok": True,
                "url": session.page.url,
                "title": title,
                "browser": session.browser_name,
            }
            if goto_result.get("wait_until_fallback"):
                result["wait_until_fallback"] = goto_result["wait_until_fallback"]
            return result

    async def _handle_info(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        del payload
        owner_id = _require_owner(context)
        await self._cleanup_expired_sessions()
        async with self._owner_lock(owner_id):
            session = self._sessions.get(owner_id)
            if session is None:
                return _browser_not_open_result("browser_info")
            title = await session.page.title()
            session.last_used_monotonic = time.monotonic()
            return {
                "ok": True,
                "url": session.page.url,
                "title": title,
                "browser": session.browser_name,
            }

    async def _handle_wait_for(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        await self._cleanup_expired_sessions()
        async with self._owner_lock(owner_id):
            selector = _coerce_non_empty_string(payload.get("selector"), "selector")
            timeout_seconds = self._coerce_timeout(
                payload.get("timeout_seconds"),
                default=self._config.action_timeout_seconds,
                field="timeout_seconds",
            )
            state = self._coerce_wait_for_state(payload.get("state"))
            session = self._sessions.get(owner_id)
            if session is None:
                return _browser_not_open_result("browser_wait_for")
            try:
                await session.page.wait_for_selector(selector, state=state, timeout=timeout_seconds * 1000)
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "selector": selector,
                    "state": state,
                    "timed_out": _is_timeout_error(exc),
                    "error": str(exc),
                    "url": session.page.url,
                }
            session.last_used_monotonic = time.monotonic()
            return {
                "ok": True,
                "selector": selector,
                "state": state,
                "url": session.page.url,
            }

    async def _handle_get_html(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        await self._cleanup_expired_sessions()
        async with self._owner_lock(owner_id):
            limit = self._coerce_limit(payload.get("limit"))
            offset = self._coerce_offset(payload.get("offset"))
            offset_type = self._coerce_offset_type(payload.get("offset_type"))
            session = self._sessions.get(owner_id)
            if session is None:
                return _browser_not_open_result("browser_get_html")
            snapshot: _ProcessedSnapshot | None = None
            if self._config.postprocess_outputs:
                snapshot = await self._get_processed_snapshot(session)
                html = snapshot.clean_html
            else:
                html = await session.page.content()
            chunk, total_units, next_offset, has_more = _slice_html(
                html,
                offset=offset,
                limit=limit,
                offset_type=offset_type,
            )
            session.last_used_monotonic = time.monotonic()
            result = {
                "ok": True,
                "offset_type": offset_type,
                "offset": offset,
                "limit": limit,
                "total_units": total_units,
                "next_offset": next_offset,
                "has_more": has_more,
                "html": chunk,
                "url": session.page.url,
            }
            if snapshot is not None:
                result["cleaned"] = True
                result["content_hash"] = snapshot.content_hash
                if self._config.postprocess_expose_raw:
                    result["raw_html"] = snapshot.raw_html[: self._config.max_text_chars]
            return result

    async def _handle_get_text(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        await self._cleanup_expired_sessions()
        async with self._owner_lock(owner_id):
            limit = self._coerce_limit(payload.get("limit"))
            offset = self._coerce_offset(payload.get("offset"))
            offset_type = self._coerce_offset_type(payload.get("offset_type"))
            session = self._sessions.get(owner_id)
            if session is None:
                return _browser_not_open_result("browser_get_text")
            snapshot: _ProcessedSnapshot | None = None
            if self._config.postprocess_outputs:
                snapshot = await self._get_processed_snapshot(session)
                text = snapshot.clean_text
            else:
                raw_text = await session.page.text_content(
                    "body",
                    timeout=self._config.action_timeout_seconds * 1000,
                )
                text = (raw_text or "").strip()
            chunk, total_units, next_offset, has_more = _slice_content(
                text,
                offset=offset,
                limit=limit,
                offset_type=offset_type,
            )
            session.last_used_monotonic = time.monotonic()
            result = {
                "ok": True,
                "offset_type": offset_type,
                "offset": offset,
                "limit": limit,
                "total_units": total_units,
                "next_offset": next_offset,
                "has_more": has_more,
                "text": chunk,
                "url": session.page.url,
            }
            if snapshot is not None:
                result["cleaned"] = True
                result["content_hash"] = snapshot.content_hash
                result["raw_chars"] = len(snapshot.raw_html)
                result["clean_chars"] = len(snapshot.clean_text)
            return result

    async def _handle_extract(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        await self._cleanup_expired_sessions()
        async with self._owner_lock(owner_id):
            selector = _coerce_non_empty_string(payload.get("selector"), "selector")
            timeout_seconds = self._coerce_timeout(
                payload.get("timeout_seconds"),
                default=self._config.action_timeout_seconds,
                field="timeout_seconds",
            )
            max_chars = self._coerce_timeout(
                payload.get("max_chars"),
                default=self._config.max_text_chars,
                field="max_chars",
            )
            session = self._sessions.get(owner_id)
            if session is None:
                return _browser_not_open_result("browser_extract")
            try:
                text = await session.page.text_content(selector, timeout=timeout_seconds * 1000)
            except Exception as exc:  # noqa: BLE001
                return {
                    "ok": False,
                    "selector": selector,
                    "timed_out": _is_timeout_error(exc),
                    "error": str(exc),
                    "url": session.page.url,
                }
            normalized = (text or "").strip()
            truncated = len(normalized) > max_chars
            session.last_used_monotonic = time.monotonic()
            return {
                "ok": True,
                "selector": selector,
                "text": normalized[:max_chars],
                "truncated": truncated,
                "url": session.page.url,
            }

    async def _handle_screenshot(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        await self._cleanup_expired_sessions()
        async with self._owner_lock(owner_id):
            image_type = self._coerce_image_type(payload.get("image_type"))
            full_page = _coerce_bool(payload.get("full_page"), default=True, field="full_page")
            return_base64 = _coerce_bool(payload.get("return_base64"), default=True, field="return_base64")
            quality = self._coerce_optional_quality(payload.get("quality"), image_type=image_type)
            session = self._require_session(owner_id)

            screenshot_kwargs: dict[str, Any] = {
                "full_page": full_page,
                "type": image_type,
            }
            if quality is not None:
                screenshot_kwargs["quality"] = quality

            raw_bytes = await session.page.screenshot(**screenshot_kwargs)
            if len(raw_bytes) > self._config.max_screenshot_bytes:
                return {
                    "ok": False,
                    "error": (
                        "screenshot exceeds configured size limit "
                        f"({len(raw_bytes)} > {self._config.max_screenshot_bytes} bytes)"
                    ),
                    "byte_size": len(raw_bytes),
                    "max_screenshot_bytes": self._config.max_screenshot_bytes,
                }
            session.last_used_monotonic = time.monotonic()

            result: dict[str, Any] = {
                "ok": True,
                "byte_size": len(raw_bytes),
                "image_type": image_type,
                "url": session.page.url,
            }
            if return_base64:
                result["image_base64"] = base64.b64encode(raw_bytes).decode("ascii")
            return result

    async def _handle_close(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        del payload
        owner_id = _require_owner(context)
        async with self._owner_lock(owner_id):
            session = self._sessions.pop(owner_id, None)
            if session is None:
                return {"ok": True, "closed": False, "browser_open": False}
            await self._close_session(session)
            return {"ok": True, "closed": True, "browser_open": False}

    async def _create_session(self, browser_name: str) -> _BrowserSession:
        playwright_factory = _load_playwright()
        manager = playwright_factory()
        playwright = await manager.start()
        browser_launcher = getattr(playwright, browser_name)
        launch_kwargs: dict[str, Any] = {
            "headless": self._config.headless,
            "args": list(self._config.launch_args),
        }
        if browser_name == "chromium" and self._config.launch_channel:
            launch_kwargs["channel"] = self._config.launch_channel
        browser = await self._launch_browser(browser_launcher, browser_name, launch_kwargs)
        context = await browser.new_context(
            user_agent=self._config.user_agent,
            viewport={"width": self._config.viewport_width, "height": self._config.viewport_height},
            locale=self._config.locale,
            timezone_id=self._config.timezone_id,
            permissions=list(self._config.permissions),
            geolocation={
                "latitude": self._config.geolocation_latitude,
                "longitude": self._config.geolocation_longitude,
            },
            screen={"width": self._config.screen_width, "height": self._config.screen_height},
            extra_http_headers=dict(self._config.extra_http_headers),
        )
        page = await context.new_page()
        return _BrowserSession(
            playwright=playwright,
            browser=browser,
            context=context,
            page=page,
            browser_name=browser_name,
            last_used_monotonic=time.monotonic(),
        )

    async def _launch_browser(self, browser_launcher: Any, browser_name: str, launch_kwargs: dict[str, Any]) -> Any:
        try:
            return await browser_launcher.launch(**launch_kwargs)
        except Exception as first_exc:  # noqa: BLE001
            if browser_name != "chromium":
                raise
            retries: list[dict[str, Any]] = []
            if launch_kwargs.get("channel"):
                without_channel = dict(launch_kwargs)
                without_channel.pop("channel", None)
                retries.append(without_channel)
                for executable_path in self._chromium_executable_candidates():
                    with_executable = dict(without_channel)
                    with_executable["executable_path"] = executable_path
                    retries.append(with_executable)
            elif not launch_kwargs.get("executable_path"):
                for executable_path in self._chromium_executable_candidates():
                    with_executable = dict(launch_kwargs)
                    with_executable["executable_path"] = executable_path
                    retries.append(with_executable)

            last_exc: Exception = first_exc
            for retry_kwargs in retries:
                try:
                    self._logger.warning(
                        "playwright chromium launch retry",
                        extra={
                            "channel": retry_kwargs.get("channel"),
                            "executable_path": retry_kwargs.get("executable_path"),
                        },
                    )
                    return await browser_launcher.launch(**retry_kwargs)
                except Exception as retry_exc:  # noqa: BLE001
                    last_exc = retry_exc
            raise RuntimeError(
                "failed to launch chromium. If you installed Debian chromium, set "
                "tools.playwright.launch_channel = '' and optionally "
                "tools.playwright.chromium_executable_path = '/usr/bin/chromium'."
            ) from last_exc

    async def _goto_with_timeout_fallback(
        self,
        page: Any,
        *,
        url: str,
        wait_until: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        timeout_ms = timeout_seconds * 1000
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            if wait_until == "networkidle" and _is_timeout_error(exc):
                fallback_wait_until = "domcontentloaded"
                self._logger.warning(
                    "playwright goto timeout on networkidle; retrying with domcontentloaded",
                    extra={
                        "url": url,
                        "timeout_seconds": timeout_seconds,
                    },
                )
                try:
                    await page.goto(url, wait_until=fallback_wait_until, timeout=timeout_ms)
                    return {
                        "ok": True,
                        "wait_until_fallback": fallback_wait_until,
                    }
                except Exception as fallback_exc:  # noqa: BLE001
                    return {
                        "ok": False,
                        "timed_out": _is_timeout_error(fallback_exc),
                        "error": str(fallback_exc),
                        "url": getattr(page, "url", ""),
                    }
            return {
                "ok": False,
                "timed_out": _is_timeout_error(exc),
                "error": str(exc),
                "url": getattr(page, "url", ""),
            }

    def _chromium_executable_candidates(self) -> list[str]:
        candidates: list[str] = []
        configured = (self._config.chromium_executable_path or "").strip()
        if configured and Path(configured).exists():
            candidates.append(configured)
        for path in [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ]:
            if Path(path).exists():
                candidates.append(path)
        return list(dict.fromkeys(candidates))

    def _require_session(self, owner_id: str) -> _BrowserSession:
        session = self._sessions.get(owner_id)
        if session is None:
            raise ValueError("browser session not started; call browser_open first")
        return session

    async def _cleanup_expired_sessions(self) -> None:
        expired_owner_ids: list[str] = []
        now = time.monotonic()
        for owner_id, session in self._sessions.items():
            age = now - session.last_used_monotonic
            if age > self._config.session_ttl_seconds:
                expired_owner_ids.append(owner_id)
        for owner_id in expired_owner_ids:
            async with self._owner_lock(owner_id):
                session = self._sessions.pop(owner_id, None)
                if session is None:
                    continue
                await self._close_session(session)

    async def _close_session(self, session: _BrowserSession) -> None:
        try:
            await session.context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await session.browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await session.playwright.stop()
        except Exception:  # noqa: BLE001
            pass

    async def _get_processed_snapshot(self, session: _BrowserSession) -> _ProcessedSnapshot:
        now = time.monotonic()
        snapshot = session.processed_snapshot
        ttl_seconds = self._config.postprocess_snapshot_ttl_seconds
        if snapshot is not None and (now - snapshot.generated_monotonic) <= ttl_seconds:
            return snapshot
        return await self._refresh_processed_snapshot(session)

    async def _refresh_processed_snapshot(self, session: _BrowserSession) -> _ProcessedSnapshot:
        try:
            raw_html = await session.page.content()
        except Exception:  # noqa: BLE001
            raw_html = ""
        snapshot = _build_processed_snapshot(raw_html)
        session.processed_snapshot = snapshot
        return snapshot

    async def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise ValueError("url must include a hostname")
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("url scheme must be http or https")
        if parsed.scheme == "http" and not self._config.allow_http:
            raise ValueError("http URLs are disabled by configuration")
        if self._config.allowed_domains and not _is_allowed_domain(host, self._config.allowed_domains):
            raise ValueError("url host is not in allowed_domains")
        if not self._config.block_private_networks:
            return
        addresses = await _resolve_ip_addresses(host)
        for address in addresses:
            if _is_private_like_ip(address):
                raise ValueError("private or local network targets are blocked")

    def _owner_lock(self, owner_id: str) -> asyncio.Lock:
        lock = self._owner_locks.get(owner_id)
        if lock is None:
            lock = asyncio.Lock()
            self._owner_locks[owner_id] = lock
        return lock

    def _coerce_browser(self, value: Any) -> str:
        if value is None:
            return self._config.browser
        if not isinstance(value, str):
            raise ValueError("browser must be a string")
        normalized = value.strip().lower()
        if normalized not in {"chromium", "firefox", "webkit"}:
            raise ValueError("browser must be one of chromium, firefox, webkit")
        return normalized

    def _coerce_url(self, value: Any) -> str:
        return _coerce_non_empty_string(value, "url")

    def _coerce_wait_until(self, value: Any) -> str:
        if value is None:
            return "domcontentloaded"
        if not isinstance(value, str):
            raise ValueError("wait_until must be a string")
        normalized = value.strip().lower()
        if normalized not in _WAIT_UNTIL_VALUES:
            raise ValueError("wait_until must be one of load, domcontentloaded, networkidle")
        return normalized

    def _coerce_wait_for_state(self, value: Any) -> str:
        if value is None:
            return "visible"
        if not isinstance(value, str):
            raise ValueError("state must be a string")
        normalized = value.strip().lower()
        if normalized not in {"attached", "detached", "visible", "hidden"}:
            raise ValueError("state must be one of attached, detached, visible, hidden")
        return normalized

    def _coerce_offset_type(self, value: Any) -> str:
        if value is None:
            return "characters"
        if not isinstance(value, str):
            raise ValueError("offset_type must be a string")
        normalized = value.strip().lower()
        if normalized not in {"characters", "lines"}:
            raise ValueError("offset_type must be one of characters or lines")
        return normalized

    def _coerce_limit(self, value: Any) -> int:
        default_limit = 4000
        max_limit = 50000
        if value is None:
            return default_limit
        if isinstance(value, bool):
            raise ValueError("limit must be numeric")
        if isinstance(value, int):
            limit = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default_limit
            limit = int(stripped)
        else:
            raise ValueError("limit must be numeric")
        if limit < 1:
            raise ValueError("limit must be >= 1")
        return min(limit, max_limit)

    def _coerce_offset(self, value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            raise ValueError("offset must be numeric")
        if isinstance(value, int):
            offset = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0
            offset = int(stripped)
        else:
            raise ValueError("offset must be numeric")
        if offset < 0:
            raise ValueError("offset must be >= 0")
        return offset

    def _coerce_timeout(self, value: Any, *, default: int, field: str) -> int:
        if value is None:
            return default
        if isinstance(value, bool):
            raise ValueError(f"{field} must be numeric")
        if isinstance(value, int):
            timeout = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default
            timeout = int(stripped)
        else:
            raise ValueError(f"{field} must be numeric")
        if timeout < 1:
            raise ValueError(f"{field} must be >= 1")
        return timeout

    def _coerce_image_type(self, value: Any) -> str:
        if value is None:
            return "png"
        if not isinstance(value, str):
            raise ValueError("image_type must be a string")
        normalized = value.strip().lower()
        if normalized not in _IMAGE_TYPES:
            raise ValueError("image_type must be png or jpeg")
        return normalized

    def _coerce_optional_quality(self, value: Any, *, image_type: str) -> int | None:
        if image_type != "jpeg":
            return None
        if value is None:
            return 80
        if isinstance(value, bool):
            raise ValueError("quality must be numeric")
        if isinstance(value, int):
            quality = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 80
            quality = int(stripped)
        else:
            raise ValueError("quality must be numeric")
        if quality < 1 or quality > 100:
            raise ValueError("quality must be between 1 and 100")
        return quality

    def _open_schema(self) -> Tool:
        return Tool(
            name="browser_open",
            description=(
                "Open a URL in a persistent browser session for this owner. "
                "Creates the session if needed and returns current page URL/title."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute URL to open."},
                    "browser": {
                        "type": ["string", "null"],
                        "description": "Optional browser engine override: chromium, firefox, or webkit.",
                    },
                    "wait_until": {
                        "type": ["string", "null"],
                        "description": "Navigation readiness: load, domcontentloaded, or networkidle.",
                    },
                    "timeout_seconds": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Optional navigation timeout in seconds.",
                    },
                },
                "required": ["url", "browser", "wait_until", "timeout_seconds"],
                "additionalProperties": False,
            },
        )

    def _click_schema(self) -> Tool:
        return Tool(
            name="browser_click",
            description="Click an element in the current page using a CSS selector.",
            parameters={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to click.",
                    },
                    "timeout_seconds": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Optional click timeout in seconds.",
                    },
                },
                "required": ["selector", "timeout_seconds"],
                "additionalProperties": False,
            },
        )

    def _navigate_schema(self) -> Tool:
        return Tool(
            name="browser_navigate",
            description="Navigate the already open browser session to another URL.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute URL to open."},
                    "wait_until": {
                        "type": ["string", "null"],
                        "description": "Navigation readiness: load, domcontentloaded, or networkidle.",
                    },
                    "timeout_seconds": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Optional navigation timeout in seconds.",
                    },
                },
                "required": ["url", "wait_until", "timeout_seconds"],
                "additionalProperties": False,
            },
        )

    def _info_schema(self) -> Tool:
        return Tool(
            name="browser_info",
            description="Return metadata for the current page in the active browser session.",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )

    def _wait_for_schema(self) -> Tool:
        return Tool(
            name="browser_wait_for",
            description="Wait for a selector on the current page before running additional actions.",
            parameters={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to wait for.",
                    },
                    "state": {
                        "type": ["string", "null"],
                        "description": "attached, detached, visible, or hidden.",
                    },
                    "timeout_seconds": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Optional wait timeout in seconds.",
                    },
                },
                "required": ["selector", "state", "timeout_seconds"],
                "additionalProperties": False,
            },
        )

    def _get_html_schema(self) -> Tool:
        return Tool(
            name="browser_get_html",
            description=(
                "Return a chunk of the current page HTML using offset+limit pagination. "
                "When postprocess_outputs is enabled, this returns cleaned HTML from the Python snapshot. "
                "Use offset_type='characters' by default for minified pages."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Chunk size. Prefer characters for minified pages.",
                    },
                    "offset": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "description": "Start offset in selected unit type.",
                    },
                    "offset_type": {
                        "type": ["string", "null"],
                        "description": "Pagination units: characters or lines (default characters).",
                    },
                },
                "required": ["limit", "offset", "offset_type"],
                "additionalProperties": False,
            },
        )

    def _get_text_schema(self) -> Tool:
        return Tool(
            name="browser_get_text",
            description=(
                "Return a chunk of visible body text from the current page using offset+limit pagination. "
                "When postprocess_outputs is enabled, text comes from Python post-processed HTML snapshots. "
                "Use offset_type='characters' by default for minified pages."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Chunk size. Prefer characters for minified pages.",
                    },
                    "offset": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "description": "Start offset in selected unit type.",
                    },
                    "offset_type": {
                        "type": ["string", "null"],
                        "description": "Pagination units: characters or lines (default characters).",
                    },
                },
                "required": ["limit", "offset", "offset_type"],
                "additionalProperties": False,
            },
        )

    def _extract_schema(self) -> Tool:
        return Tool(
            name="browser_extract",
            description="Extract text content from a CSS selector in the current page.",
            parameters={
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to read text from.",
                    },
                    "timeout_seconds": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Optional wait timeout in seconds.",
                    },
                    "max_chars": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Optional response cap for extracted text.",
                    },
                },
                "required": ["selector", "timeout_seconds", "max_chars"],
                "additionalProperties": False,
            },
        )

    def _screenshot_schema(self) -> Tool:
        return Tool(
            name="browser_screenshot",
            description="Capture a screenshot of the current page.",
            parameters={
                "type": "object",
                "properties": {
                    "full_page": {
                        "type": ["boolean", "null"],
                        "description": "Capture full page instead of viewport.",
                    },
                    "image_type": {
                        "type": ["string", "null"],
                        "description": "png or jpeg.",
                    },
                    "quality": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 100,
                        "description": "JPEG quality (ignored for png).",
                    },
                    "return_base64": {
                        "type": ["boolean", "null"],
                        "description": "When true, include base64 image payload.",
                    },
                },
                "required": ["full_page", "image_type", "quality", "return_base64"],
                "additionalProperties": False,
            },
        )

    def _close_schema(self) -> Tool:
        return Tool(
            name="browser_close",
            description=(
                "Close the active browser session for this owner. "
                "Use only when explicitly requested by the user."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )

    def _close_quick_schema(self) -> Tool:
        return Tool(
            name="browser_close_session",
            description=(
                "Quick alias to close browser session. "
                "Use only when explicitly requested by the user."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )


def _require_owner(context: ToolContext) -> str:
    if not context.owner_id:
        raise ValueError("owner context is required")
    return context.owner_id


def _coerce_non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _coerce_bool(value: Any, *, default: bool, field: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"{field} must be boolean")


def _browser_not_open_result(action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "browser_open": False,
        "action": action,
        "error": "browser session not started; call browser_open first",
    }


def _is_allowed_domain(hostname: str, allowed_domains: list[str]) -> bool:
    normalized_host = hostname.strip().lower().rstrip(".")
    for domain in allowed_domains:
        candidate = domain.strip().lower().rstrip(".")
        if not candidate:
            continue
        if normalized_host == candidate or normalized_host.endswith(f".{candidate}"):
            return True
    return False


def _is_private_like_ip(ip_text: str) -> bool:
    addr = ipaddress.ip_address(ip_text)
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _is_timeout_error(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    return "timeout" in name


async def _resolve_ip_addresses(hostname: str) -> list[str]:
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(hostname, None, proto=0)
    except Exception:
        return []
    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_text = sockaddr[0]
        if isinstance(ip_text, str):
            addresses.append(ip_text)
    return list(dict.fromkeys(addresses))


def _build_processed_snapshot(raw_html: str) -> _ProcessedSnapshot:
    clean_html = _clean_html_for_llm(raw_html)
    clean_text = _extract_text_from_html(clean_html)
    if not clean_text:
        clean_text = _extract_text_from_html(raw_html)
    content_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()[:16]
    return _ProcessedSnapshot(
        raw_html=raw_html,
        clean_html=clean_html,
        clean_text=clean_text,
        content_hash=content_hash,
        generated_monotonic=time.monotonic(),
    )


def _clean_html_for_llm(raw_html: str) -> str:
    if not raw_html:
        return ""
    cleaned = re.sub(r"<!--.*?-->", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = _drop_wrapped_tags(cleaned, _DROP_BLOCK_TAGS)
    cleaned = _drop_wrapped_tags(cleaned, _DROP_LAYOUT_TAGS)
    cleaned = _drop_low_signal_blocks(cleaned)
    cleaned = re.sub(r"[ \t\f\v]+", " ", cleaned)
    return cleaned.strip()


def _drop_wrapped_tags(content: str, tag_names: tuple[str, ...]) -> str:
    if not content:
        return content
    tags = "|".join(tag_names)
    pattern = rf"<(?P<tag>{tags})\b[^>]*>.*?</(?P=tag)\s*>"
    return re.sub(pattern, " ", content, flags=re.IGNORECASE | re.DOTALL)


def _drop_low_signal_blocks(content: str) -> str:
    if not content:
        return content
    pattern = (
        r"<(?P<tag>[a-z0-9]+)\b"
        r"(?=[^>]*(?:id|class)\s*=\s*['\"][^'\"]*(?:cookie|consent|banner|ads?|advert|newsletter|subscribe|social|breadcrumb)[^'\"]*['\"])[^>]*>"
        r".*?</(?P=tag)\s*>"
    )
    cleaned = re.sub(pattern, " ", content, flags=re.IGNORECASE | re.DOTALL)
    return cleaned


def _extract_text_from_html(source_html: str) -> str:
    if not source_html:
        return ""
    block_tags = "p|div|section|article|li|h1|h2|h3|h4|h5|h6|tr|td|th|br|main"
    block_split = re.sub(rf"</?(?:{block_tags})\b[^>]*>", "\n", source_html, flags=re.IGNORECASE)
    stripped = re.sub(r"<[^>]+>", " ", block_split)
    unescaped = html.unescape(stripped)
    lines = [re.sub(r"\s+", " ", line).strip() for line in unescaped.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _slice_content(content: str, *, offset: int, limit: int, offset_type: str) -> tuple[str, int, int | None, bool]:
    if offset_type == "lines":
        lines = content.splitlines()
        total = len(lines)
        start = min(offset, total)
        end = min(start + limit, total)
        chunk = "\n".join(lines[start:end])
        has_more = end < total
        return chunk, total, (end if has_more else None), has_more

    total = len(content)
    start = min(offset, total)
    end = min(start + limit, total)
    chunk = content[start:end]
    has_more = end < total
    return chunk, total, (end if has_more else None), has_more


def _slice_html(html: str, *, offset: int, limit: int, offset_type: str) -> tuple[str, int, int | None, bool]:
    return _slice_content(html, offset=offset, limit=limit, offset_type=offset_type)
