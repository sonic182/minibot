from __future__ import annotations

from typing import Any

import pytest

from minibot.adapters.config.schema import PlaywrightToolConfig
from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.playwright import PlaywrightTool
import minibot.llm.tools.playwright as playwright_module


class _FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self._title = "Blank"

    async def goto(self, url: str, wait_until: str, timeout: int) -> None:
        del wait_until, timeout
        self.url = url
        self._title = "Example Domain"

    async def title(self) -> str:
        return self._title

    async def click(self, selector: str, timeout: int) -> None:
        del selector, timeout

    async def text_content(self, selector: str, timeout: int) -> str:
        del timeout
        if selector == "#phrase":
            raise TimeoutError("waiting for locator('#phrase')")
        if selector == "body":
            return "Line1\nLine2\nLine3"
        if selector == "h1":
            return "Example Domain"
        return ""

    async def screenshot(self, **kwargs: Any) -> bytes:
        del kwargs
        return b"PNGDATA"

    async def content(self) -> str:
        return "<html><body>Line1\nLine2\nLine3</body></html>"

    async def wait_for_selector(self, selector: str, state: str, timeout: int) -> None:
        del timeout
        if selector == "#missing":
            raise TimeoutError(f"waiting for selector '{selector}' in state '{state}'")


class _FakeContext:
    async def new_page(self) -> _FakePage:
        return _FakePage()

    async def close(self) -> None:
        return None


class _FakeBrowser:
    async def new_context(self, **kwargs: Any) -> _FakeContext:
        del kwargs
        return _FakeContext()

    async def close(self) -> None:
        return None


class _FakeLauncher:
    async def launch(self, **kwargs: Any) -> _FakeBrowser:
        del kwargs
        return _FakeBrowser()


class _FailingChannelLauncher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.calls.append(dict(kwargs))
        if kwargs.get("channel") == "chrome":
            raise RuntimeError("Chromium distribution 'chrome' is not found")
        return _FakeBrowser()


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium: Any = _FakeLauncher()
        self.firefox: Any = _FakeLauncher()
        self.webkit: Any = _FakeLauncher()

    async def stop(self) -> None:
        return None


class _FakeManager:
    def __init__(self, playwright: _FakePlaywright) -> None:
        self._playwright = playwright

    async def start(self) -> _FakePlaywright:
        return self._playwright


@pytest.mark.asyncio
async def test_playwright_tool_open_extract_navigate_info_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_playwright = _FakePlaywright()
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: (lambda: _FakeManager(fake_playwright)),
    )
    config = PlaywrightToolConfig(
        enabled=True,
        browser="firefox",
        allow_http=True,
        block_private_networks=False,
        max_screenshot_bytes=1024,
    )
    bindings = {binding.tool.name: binding for binding in PlaywrightTool(config).bindings()}
    context = ToolContext(owner_id="owner-1")

    opened = await bindings["browser_open"].handler(
        {
            "url": "http://example.com",
            "browser": "firefox",
            "wait_until": None,
            "timeout_seconds": None,
        },
        context,
    )
    assert opened["ok"] is True
    assert opened["browser"] == "firefox"
    assert opened["title"] == "Example Domain"

    extracted = await bindings["browser_extract"].handler(
        {"selector": "h1", "timeout_seconds": None, "max_chars": None},
        context,
    )
    assert extracted["ok"] is True
    assert extracted["text"] == "Example Domain"

    waited = await bindings["browser_wait_for"].handler(
        {"selector": "h1", "state": "visible", "timeout_seconds": None},
        context,
    )
    assert waited["ok"] is True
    assert waited["state"] == "visible"

    html_chars = await bindings["browser_get_html"].handler(
        {"offset": 0, "limit": 12, "offset_type": "characters"},
        context,
    )
    assert html_chars["ok"] is True
    assert html_chars["offset_type"] == "characters"
    assert html_chars["has_more"] is True

    html_lines = await bindings["browser_get_html"].handler(
        {"offset": 1, "limit": 2, "offset_type": "lines"},
        context,
    )
    assert html_lines["ok"] is True
    assert html_lines["offset_type"] == "lines"

    text_chars = await bindings["browser_get_text"].handler(
        {"offset": 0, "limit": 5, "offset_type": "characters"},
        context,
    )
    assert text_chars["ok"] is True
    assert text_chars["text"] == "Line1"

    text_lines = await bindings["browser_get_text"].handler(
        {"offset": 1, "limit": 2, "offset_type": "lines"},
        context,
    )
    assert text_lines["ok"] is True
    assert text_lines["text"] == "Line2\nLine3"

    navigated = await bindings["browser_navigate"].handler(
        {
            "url": "http://example.com/again",
            "wait_until": None,
            "timeout_seconds": None,
        },
        context,
    )
    assert navigated["ok"] is True
    assert navigated["url"] == "http://example.com/again"

    info = await bindings["browser_info"].handler(
        {},
        context,
    )
    assert info["ok"] is True
    assert info["title"] == "Example Domain"

    closed = await bindings["browser_close"].handler({}, context)
    assert closed == {"ok": True, "closed": True, "browser_open": False}


@pytest.mark.asyncio
async def test_playwright_tool_enforces_allowed_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_playwright = _FakePlaywright()
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: (lambda: _FakeManager(fake_playwright)),
    )
    config = PlaywrightToolConfig(
        enabled=True,
        allowed_domains=["example.com"],
        block_private_networks=False,
    )
    bindings = {binding.tool.name: binding for binding in PlaywrightTool(config).bindings()}
    context = ToolContext(owner_id="owner-1")

    with pytest.raises(ValueError, match="allowed_domains"):
        await bindings["browser_open"].handler(
            {
                "url": "https://evil.test",
                "browser": None,
                "wait_until": None,
                "timeout_seconds": None,
            },
            context,
        )


@pytest.mark.asyncio
async def test_playwright_tool_blocks_private_networks(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_playwright = _FakePlaywright()
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: (lambda: _FakeManager(fake_playwright)),
    )
    async def _fake_resolve(hostname: str) -> list[str]:
        del hostname
        return ["127.0.0.1"]

    monkeypatch.setattr(playwright_module, "_resolve_ip_addresses", _fake_resolve)
    config = PlaywrightToolConfig(enabled=True, allow_http=True, block_private_networks=True)
    bindings = {binding.tool.name: binding for binding in PlaywrightTool(config).bindings()}
    context = ToolContext(owner_id="owner-1")

    with pytest.raises(ValueError, match="private or local"):
        await bindings["browser_open"].handler(
            {
                "url": "http://localhost:8080",
                "browser": None,
                "wait_until": None,
                "timeout_seconds": None,
            },
            context,
        )


@pytest.mark.asyncio
async def test_playwright_tool_extract_returns_structured_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_playwright = _FakePlaywright()
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: (lambda: _FakeManager(fake_playwright)),
    )
    config = PlaywrightToolConfig(
        enabled=True,
        allow_http=True,
        block_private_networks=False,
    )
    bindings = {binding.tool.name: binding for binding in PlaywrightTool(config).bindings()}
    context = ToolContext(owner_id="owner-1")

    await bindings["browser_open"].handler(
        {
            "url": "http://example.com",
            "browser": "chromium",
            "wait_until": None,
            "timeout_seconds": None,
        },
        context,
    )
    result = await bindings["browser_extract"].handler(
        {"selector": "#phrase", "timeout_seconds": None, "max_chars": None},
        context,
    )
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["selector"] == "#phrase"


@pytest.mark.asyncio
async def test_playwright_actions_report_when_session_not_open(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_playwright = _FakePlaywright()
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: (lambda: _FakeManager(fake_playwright)),
    )
    config = PlaywrightToolConfig(enabled=True, allow_http=True, block_private_networks=False)
    bindings = {binding.tool.name: binding for binding in PlaywrightTool(config).bindings()}
    context = ToolContext(owner_id="owner-1")

    for name, payload in [
        ("browser_info", {}),
        ("browser_navigate", {"url": "http://example.com", "wait_until": None, "timeout_seconds": None}),
        ("browser_get_html", {"offset": 0, "limit": 100, "offset_type": "characters"}),
        ("browser_get_text", {"offset": 0, "limit": 100, "offset_type": "characters"}),
        ("browser_wait_for", {"selector": "h1", "state": None, "timeout_seconds": None}),
        ("browser_click", {"selector": "button", "timeout_seconds": None}),
        ("browser_extract", {"selector": "h1", "timeout_seconds": None, "max_chars": None}),
    ]:
        result = await bindings[name].handler(payload, context)
        assert result["ok"] is False
        assert result["browser_open"] is False

    closed = await bindings["browser_close"].handler({}, context)
    assert closed == {"ok": True, "closed": False, "browser_open": False}


@pytest.mark.asyncio
async def test_playwright_tool_retries_without_channel_on_chromium(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_playwright = _FakePlaywright()
    failing_launcher = _FailingChannelLauncher()
    fake_playwright.chromium = failing_launcher
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: (lambda: _FakeManager(fake_playwright)),
    )
    config = PlaywrightToolConfig(
        enabled=True,
        browser="chromium",
        launch_channel="chrome",
        allow_http=True,
        block_private_networks=False,
    )
    bindings = {binding.tool.name: binding for binding in PlaywrightTool(config).bindings()}
    context = ToolContext(owner_id="owner-1")

    opened = await bindings["browser_open"].handler(
        {
            "url": "http://example.com",
            "browser": "chromium",
            "wait_until": None,
            "timeout_seconds": None,
        },
        context,
    )
    assert opened["ok"] is True
    assert len(failing_launcher.calls) >= 2
    assert failing_launcher.calls[0].get("channel") == "chrome"
    assert failing_launcher.calls[1].get("channel") is None
