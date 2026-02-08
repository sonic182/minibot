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


class _TimeoutOnNetworkIdlePage(_FakePage):
    async def goto(self, url: str, wait_until: str, timeout: int) -> None:
        del timeout
        if wait_until == "networkidle":
            raise TimeoutError("Page.goto: Timeout 60000ms exceeded")
        self.url = url
        self._title = "Recovered"


class _AlwaysTimeoutPage(_FakePage):
    async def goto(self, url: str, wait_until: str, timeout: int) -> None:
        del wait_until, timeout
        self.url = url
        self._title = "Partial"
        raise TimeoutError("Page.goto: Timeout 30000ms exceeded")


class _RecordingTimeoutPage(_FakePage):
    def __init__(self) -> None:
        super().__init__()
        self.timeouts: list[int] = []

    async def goto(self, url: str, wait_until: str, timeout: int) -> None:
        del wait_until
        self.timeouts.append(timeout)
        self.url = url
        self._title = "Loaded"


class _NoisyPage(_FakePage):
    async def content(self) -> str:
        return (
            "<html><body>"
            "<nav>Menu links</nav>"
            "<article>"
            "<h1>Headline</h1>"
            "<a href='/listing?id=123&amp;ref=srp'>Toyota\u200b RAV 4</a>"
            "<p>Important&nbsp;body text • gasolina</p>"
            "</article>"
            "<script>console.log('tracking')</script>"
            "<footer>Footer links</footer>"
            "</body></html>"
        )


class _ClosedTargetPage(_FakePage):
    async def goto(self, url: str, wait_until: str, timeout: int) -> None:
        del url, wait_until, timeout
        raise RuntimeError("Target page, context or browser has been closed")

    async def title(self) -> str:
        raise RuntimeError("Target page, context or browser has been closed")

    async def click(self, selector: str, timeout: int) -> None:
        del selector, timeout
        raise RuntimeError("Target page, context or browser has been closed")

    async def text_content(self, selector: str, timeout: int) -> str:
        del selector, timeout
        raise RuntimeError("Target page, context or browser has been closed")

    async def content(self) -> str:
        raise RuntimeError("Target page, context or browser has been closed")

    async def wait_for_selector(self, selector: str, state: str, timeout: int) -> None:
        del selector, state, timeout
        raise RuntimeError("Target page, context or browser has been closed")


class _FakeContext:
    async def new_page(self) -> _FakePage:
        return _FakePage()

    async def close(self) -> None:
        return None


class _TimeoutOnNetworkIdleContext(_FakeContext):
    async def new_page(self) -> _TimeoutOnNetworkIdlePage:
        return _TimeoutOnNetworkIdlePage()


class _AlwaysTimeoutContext(_FakeContext):
    async def new_page(self) -> _AlwaysTimeoutPage:
        return _AlwaysTimeoutPage()


class _RecordingTimeoutContext(_FakeContext):
    def __init__(self, page: _RecordingTimeoutPage) -> None:
        self._page = page

    async def new_page(self) -> _RecordingTimeoutPage:
        return self._page


class _NoisyContext(_FakeContext):
    async def new_page(self) -> _NoisyPage:
        return _NoisyPage()


class _ClosedTargetContext(_FakeContext):
    async def new_page(self) -> _ClosedTargetPage:
        return _ClosedTargetPage()


class _FakeBrowser:
    async def new_context(self, **kwargs: Any) -> _FakeContext:
        del kwargs
        return _FakeContext()

    async def close(self) -> None:
        return None


class _TimeoutOnNetworkIdleBrowser(_FakeBrowser):
    async def new_context(self, **kwargs: Any) -> _TimeoutOnNetworkIdleContext:
        del kwargs
        return _TimeoutOnNetworkIdleContext()


class _AlwaysTimeoutBrowser(_FakeBrowser):
    async def new_context(self, **kwargs: Any) -> _AlwaysTimeoutContext:
        del kwargs
        return _AlwaysTimeoutContext()


class _RecordingTimeoutBrowser(_FakeBrowser):
    def __init__(self, page: _RecordingTimeoutPage) -> None:
        self._page = page

    async def new_context(self, **kwargs: Any) -> _RecordingTimeoutContext:
        del kwargs
        return _RecordingTimeoutContext(self._page)


class _NoisyBrowser(_FakeBrowser):
    async def new_context(self, **kwargs: Any) -> _NoisyContext:
        del kwargs
        return _NoisyContext()


class _ClosedTargetBrowser(_FakeBrowser):
    async def new_context(self, **kwargs: Any) -> _ClosedTargetContext:
        del kwargs
        return _ClosedTargetContext()


class _FakeLauncher:
    async def launch(self, **kwargs: Any) -> _FakeBrowser:
        del kwargs
        return _FakeBrowser()


class _TimeoutOnNetworkIdleLauncher(_FakeLauncher):
    async def launch(self, **kwargs: Any) -> _TimeoutOnNetworkIdleBrowser:
        del kwargs
        return _TimeoutOnNetworkIdleBrowser()


class _AlwaysTimeoutLauncher(_FakeLauncher):
    async def launch(self, **kwargs: Any) -> _AlwaysTimeoutBrowser:
        del kwargs
        return _AlwaysTimeoutBrowser()


class _RecordingTimeoutLauncher(_FakeLauncher):
    def __init__(self, page: _RecordingTimeoutPage) -> None:
        self._page = page

    async def launch(self, **kwargs: Any) -> _RecordingTimeoutBrowser:
        del kwargs
        return _RecordingTimeoutBrowser(self._page)


class _NoisyLauncher(_FakeLauncher):
    async def launch(self, **kwargs: Any) -> _NoisyBrowser:
        del kwargs
        return _NoisyBrowser()


class _ClosedThenHealthyLauncher:
    def __init__(self) -> None:
        self.calls = 0

    async def launch(self, **kwargs: Any) -> _FakeBrowser:
        del kwargs
        self.calls += 1
        if self.calls == 1:
            return _ClosedTargetBrowser()
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


class _TimeoutOnNetworkIdlePlaywright(_FakePlaywright):
    def __init__(self) -> None:
        super().__init__()
        self.chromium = _TimeoutOnNetworkIdleLauncher()


class _AlwaysTimeoutPlaywright(_FakePlaywright):
    def __init__(self) -> None:
        super().__init__()
        self.chromium = _AlwaysTimeoutLauncher()


class _RecordingTimeoutPlaywright(_FakePlaywright):
    def __init__(self, page: _RecordingTimeoutPage) -> None:
        super().__init__()
        self.chromium = _RecordingTimeoutLauncher(page)


class _NoisyPlaywright(_FakePlaywright):
    def __init__(self) -> None:
        super().__init__()
        self.chromium = _NoisyLauncher()


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
        lambda: lambda: _FakeManager(fake_playwright),
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

    closed_alias = await bindings["browser_close_session"].handler({}, context)
    assert closed_alias == {"ok": True, "closed": False, "browser_open": False}


@pytest.mark.asyncio
async def test_playwright_open_retries_networkidle_timeout_with_domcontentloaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_playwright = _TimeoutOnNetworkIdlePlaywright()
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: lambda: _FakeManager(fake_playwright),
    )
    config = PlaywrightToolConfig(
        enabled=True,
        browser="chromium",
        allow_http=True,
        block_private_networks=False,
    )
    bindings = {binding.tool.name: binding for binding in PlaywrightTool(config).bindings()}
    context = ToolContext(owner_id="owner-1")

    opened = await bindings["browser_open"].handler(
        {
            "url": "http://example.com",
            "browser": "chromium",
            "wait_until": "networkidle",
            "timeout_seconds": 60,
        },
        context,
    )

    assert opened["ok"] is True
    assert opened["title"] == "Recovered"
    assert opened["wait_until_fallback"] == "domcontentloaded"


@pytest.mark.asyncio
async def test_playwright_open_returns_partial_result_when_navigation_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_playwright = _AlwaysTimeoutPlaywright()
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: lambda: _FakeManager(fake_playwright),
    )
    config = PlaywrightToolConfig(
        enabled=True,
        browser="chromium",
        allow_http=True,
        block_private_networks=False,
    )
    bindings = {binding.tool.name: binding for binding in PlaywrightTool(config).bindings()}
    context = ToolContext(owner_id="owner-1")

    opened = await bindings["browser_open"].handler(
        {
            "url": "http://example.com",
            "browser": "chromium",
            "wait_until": "domcontentloaded",
            "timeout_seconds": 60,
        },
        context,
    )

    assert opened["ok"] is True
    assert opened["navigation_timed_out"] is True
    assert opened["title"] == "Partial"


@pytest.mark.asyncio
async def test_playwright_open_caps_navigation_timeout_to_30_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    recording_page = _RecordingTimeoutPage()
    fake_playwright = _RecordingTimeoutPlaywright(recording_page)
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: lambda: _FakeManager(fake_playwright),
    )
    config = PlaywrightToolConfig(
        enabled=True,
        browser="chromium",
        allow_http=True,
        block_private_networks=False,
    )
    bindings = {binding.tool.name: binding for binding in PlaywrightTool(config).bindings()}
    context = ToolContext(owner_id="owner-1")

    opened = await bindings["browser_open"].handler(
        {
            "url": "http://example.com",
            "browser": "chromium",
            "wait_until": "domcontentloaded",
            "timeout_seconds": 120,
        },
        context,
    )

    assert opened["ok"] is True
    assert recording_page.timeouts
    assert recording_page.timeouts[-1] == 30000


@pytest.mark.asyncio
async def test_playwright_open_recovers_from_closed_target_by_recreating_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_playwright = _FakePlaywright()
    flaky_launcher = _ClosedThenHealthyLauncher()
    fake_playwright.chromium = flaky_launcher
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: lambda: _FakeManager(fake_playwright),
    )
    config = PlaywrightToolConfig(
        enabled=True,
        browser="chromium",
        allow_http=True,
        block_private_networks=False,
    )
    bindings = {binding.tool.name: binding for binding in PlaywrightTool(config).bindings()}
    context = ToolContext(owner_id="owner-1")

    opened = await bindings["browser_open"].handler(
        {
            "url": "http://example.com",
            "browser": "chromium",
            "wait_until": "domcontentloaded",
            "timeout_seconds": 30,
        },
        context,
    )

    assert opened["ok"] is True
    assert opened["title"] == "Example Domain"
    assert flaky_launcher.calls >= 2


@pytest.mark.asyncio
async def test_playwright_get_text_recovers_from_closed_target_session(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_playwright = _FakePlaywright()
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: lambda: _FakeManager(fake_playwright),
    )
    config = PlaywrightToolConfig(
        enabled=True,
        browser="chromium",
        allow_http=True,
        block_private_networks=False,
    )
    tool = PlaywrightTool(config)
    bindings = {binding.tool.name: binding for binding in tool.bindings()}
    context = ToolContext(owner_id="owner-1")

    opened = await bindings["browser_open"].handler(
        {
            "url": "http://example.com",
            "browser": "chromium",
            "wait_until": "domcontentloaded",
            "timeout_seconds": 30,
        },
        context,
    )
    assert opened["ok"] is True

    tool._sessions["owner-1"].page = _ClosedTargetPage()
    tool._sessions["owner-1"].processed_snapshot = None

    text_result = await bindings["browser_get_text"].handler(
        {"offset": 0, "limit": 1000, "offset_type": "characters"},
        context,
    )
    assert text_result["ok"] is True
    assert text_result["cleaned"] is True
    assert "Line1" in text_result["text"]


@pytest.mark.asyncio
async def test_playwright_get_text_and_html_use_postprocessed_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_playwright = _NoisyPlaywright()
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: lambda: _FakeManager(fake_playwright),
    )
    config = PlaywrightToolConfig(
        enabled=True,
        browser="chromium",
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

    html_result = await bindings["browser_get_html"].handler(
        {"offset": 0, "limit": 5000, "offset_type": "characters"},
        context,
    )
    assert html_result["ok"] is True
    assert html_result["cleaned"] is True
    assert "<script" not in html_result["html"].lower()

    text_result = await bindings["browser_get_text"].handler(
        {"offset": 0, "limit": 5000, "offset_type": "characters"},
        context,
    )
    assert text_result["ok"] is True
    assert text_result["cleaned"] is True
    assert text_result["text_format"] == "markdown"
    assert "Headline" in text_result["text"]
    assert "Important body text" in text_result["text"]
    assert " | gasolina" in text_result["text"]
    assert "[Toyota RAV 4](/listing?id=123&ref=srp)" in text_result["text"]
    assert "tracking" not in text_result["text"]
    assert "Menu links" not in text_result["text"]
    assert text_result["links"]
    assert text_result["links"][0]["href"] == "/listing?id=123&ref=srp"
    assert "\u200b" not in text_result["links"][0]["text"]
    assert isinstance(text_result.get("content_hash"), str)


@pytest.mark.asyncio
async def test_playwright_tool_enforces_allowed_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_playwright = _FakePlaywright()
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: lambda: _FakeManager(fake_playwright),
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
        lambda: lambda: _FakeManager(fake_playwright),
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
        lambda: lambda: _FakeManager(fake_playwright),
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
        lambda: lambda: _FakeManager(fake_playwright),
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

    closed_alias = await bindings["browser_close_session"].handler({}, context)
    assert closed_alias == {"ok": True, "closed": False, "browser_open": False}


@pytest.mark.asyncio
async def test_playwright_tool_retries_without_channel_on_chromium(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_playwright = _FakePlaywright()
    failing_launcher = _FailingChannelLauncher()
    fake_playwright.chromium = failing_launcher
    monkeypatch.setattr(
        playwright_module,
        "_load_playwright",
        lambda: lambda: _FakeManager(fake_playwright),
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
