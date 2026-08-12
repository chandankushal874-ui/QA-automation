"""Tests for the Playwright browser-context factory."""

from __future__ import annotations

import pytest

from src.browser.factory import BrowserConfig, BrowserContextFactory, InstrumentedPage
from src.core.console_capture import ConsoleCapture, ConsoleCategory, ConsoleSeverity
from src.core.endpoint_registry import EndpointRegistry


def test_browser_config_defaults():
    config = BrowserConfig()
    assert config.engine == "chromium"
    assert config.headless is True
    assert config.viewport_width == 1280
    assert config.viewport_height == 720
    assert config.ignore_https_errors is False


@pytest.mark.asyncio
@pytest.mark.browser
async def test_browser_factory_creation():
    """Test creating page and factory instrumentations.

    This uses mark 'browser' which requires playwright browsers to be installed.
    """
    async with BrowserContextFactory() as factory:
        config = BrowserConfig(engine="chromium", headless=True)
        registry = EndpointRegistry(base_url="https://example.com")

        # Create instrumented page
        inst_page = await factory.create_page(config, endpoint_registry=registry)

        # Navigate to a simple blank page or data URL to avoid external network dependencies
        await inst_page.goto("data:text/html,<html><body><h1>Hello World</h1><a href='/about'>About</a></body></html>")

        # Trigger a client-side fetch to test the network request interceptor
        await inst_page.page.evaluate("fetch('https://example.com/about', { mode: 'no-cors' }).catch(() => {})")

        # Check that page load didn't crash and we initialized listeners
        assert inst_page.page.url.startswith("data:text/html")

        # Discovered endpoints should contain the relative '/about' resolved to the base url
        discovered = registry.get_all()
        assert len(discovered) >= 1
        assert any(ep.path == "/about" for ep in discovered)

        await inst_page.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_browser_factory_matrix():
    """Test creating contexts for a browser matrix."""
    async with BrowserContextFactory() as factory:
        matrix = {"chromium": True, "firefox": False, "webkit": False}
        registry = EndpointRegistry()

        pages = await factory.create_pages_for_matrix(browser_matrix=matrix, endpoint_registry=registry, headless=True)

        assert "chromium" in pages
        assert "firefox" not in pages
        assert "webkit" not in pages

        # Verify page is instrumented
        chromium_page = pages["chromium"]
        assert chromium_page.browser_engine == "chromium"
        assert chromium_page.endpoints == registry

        await chromium_page.close()


class _FakePage:
    """Minimal stand-in for a Playwright Page (no browser required).

    Records event subscriptions so we can assert the listener wiring and
    invoke handlers directly.
    """

    def __init__(self):
        self.url = "https://app.example.com/"
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler


class _FakeRequest:
    def __init__(self, url, method="GET", resource_type="", failure=None):
        self.url = url
        self.method = method
        self.resource_type = resource_type
        self.failure = failure


def _make_instrumented_page():
    fake_page = _FakePage()
    capture = ConsoleCapture(url=fake_page.url, browser="chromium")
    registry = EndpointRegistry(base_url="https://app.example.com")
    inst = InstrumentedPage(
        page=fake_page,
        console_capture=capture,
        endpoint_registry=registry,
        browser_engine="chromium",
    )
    return inst, fake_page, capture


def test_requestfailed_listener_is_registered():
    """The requestfailed event must be wired up alongside the existing ones."""
    _, fake_page, _ = _make_instrumented_page()
    for event in ("console", "pageerror", "request", "response", "requestfailed"):
        assert event in fake_page.handlers


def test_request_failed_records_network_failure_for_api_request():
    """A failed XHR/fetch with no response is captured as a NETWORK_FAILURE."""
    inst, _, capture = _make_instrumented_page()
    req = _FakeRequest(
        url="https://api.example.com/v1/orders",
        method="POST",
        resource_type="fetch",
        failure="net::ERR_CONNECTION_REFUSED",
    )

    inst._on_request_failed(req)

    entries = capture.report.get_entries_by_category(ConsoleCategory.NETWORK_FAILURE)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.extra["status_code"] == 0
    assert entry.extra["method"] == "POST"
    assert "ERR_CONNECTION_REFUSED" in entry.message


def test_request_failed_records_resource_failure_for_asset():
    """A failed asset load (broken image) routes to add_resource_failure()."""
    inst, _, capture = _make_instrumented_page()
    req = _FakeRequest(
        url="https://cdn.example.com/logo.png",
        method="GET",
        resource_type="image",
        failure="net::ERR_NAME_NOT_RESOLVED",
    )

    inst._on_request_failed(req)

    entries = capture.report.get_entries_by_category(ConsoleCategory.RESOURCE_LOAD_FAILURE)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.extra["resource_type"] == "image"
    assert entry.severity == ConsoleSeverity.ERROR


def test_request_failed_handles_none_failure_gracefully():
    """A None failure string must not raise and still records an entry."""
    inst, _, capture = _make_instrumented_page()
    req = _FakeRequest(
        url="https://api.example.com/v1/ping",
        method="GET",
        resource_type="xhr",
        failure=None,
    )

    inst._on_request_failed(req)

    entries = capture.report.get_entries_by_category(ConsoleCategory.NETWORK_FAILURE)
    assert len(entries) == 1
    assert "request failed" in entries[0].message
