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
        if selector == "h1":
            return "Example Domain"
        return ""

    async def screenshot(self, **kwargs: Any) -> bytes:
        del kwargs
        return b"PNGDATA"


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


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeLauncher()
        self.firefox = _FakeLauncher()
        self.webkit = _FakeLauncher()

    async def stop(self) -> None:
        return None


class _FakeManager:
    def __init__(self, playwright: _FakePlaywright) -> None:
        self._playwright = playwright

    async def start(self) -> _FakePlaywright:
        return self._playwright


@pytest.mark.asyncio
async def test_playwright_tool_open_extract_screenshot_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
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

    shot = await bindings["browser_screenshot"].handler(
        {"full_page": True, "image_type": "png", "quality": None, "return_base64": False},
        context,
    )
    assert shot["ok"] is True
    assert shot["byte_size"] == 7

    closed = await bindings["browser_close"].handler({}, context)
    assert closed == {"ok": True, "closed": True}


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
