"""Playwright browser-context factory with built-in console capture and request interception.

Every browser context produced by this factory automatically:
1. Captures ALL console messages (errors, warnings, logs)
2. Intercepts ALL network requests for endpoint discovery
3. Captures page errors (uncaught exceptions, unhandled rejections)
4. Supports storageState injection for session reuse
5. Records Playwright traces for post-mortem debugging
6. Opens CDP sessions for performance metric extraction (Chromium only)

Spec reference: master-build-prompt §3, Step F
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

from src.core.console_capture import ConsoleCapture
from src.core.endpoint_registry import EndpointRegistry, EndpointSource

logger = logging.getLogger(__name__)

# Curated device descriptors (Playwright emulation — NOT real hardware).
# Name → {user_agent, viewport, device_scale_factor, is_mobile, has_touch}.
# Sourced from Playwright's canonical device registry; kept as an explicit
# allow-list so the API can advertise exactly what's supported.
DEVICE_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "iPhone 13": {
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
        "default_browser": "webkit",
    },
    "Pixel 5": {
        "user_agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/90.0.4430.91 Mobile Safari/537.36",
        "viewport": {"width": 393, "height": 851},
        "device_scale_factor": 2.75,
        "is_mobile": True,
        "has_touch": True,
        "default_browser": "chromium",
    },
    "iPad (gen 7)": {
        "user_agent": "Mozilla/5.0 (iPad; CPU OS 13_2 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/13.0 Mobile/15E148 Safari/604.1",
        "viewport": {"width": 810, "height": 1080},
        "device_scale_factor": 2,
        "is_mobile": True,
        "has_touch": True,
        "default_browser": "webkit",
    },
    "Desktop Chrome HiDPI": {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720},
        "device_scale_factor": 2,
        "is_mobile": False,
        "has_touch": False,
        "default_browser": "chromium",
    },
    "Desktop Firefox": {
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
        "viewport": {"width": 1280, "height": 720},
        "device_scale_factor": 1,
        "is_mobile": False,
        "has_touch": False,
        "default_browser": "firefox",
    },
}


def resolve_device(name: str) -> dict[str, Any] | None:
    """Return the descriptor dict for a device name, or None if unknown."""
    return DEVICE_DESCRIPTORS.get(name)


@dataclass
class BrowserConfig:
    """Configuration for a browser context."""

    engine: str = "chromium"  # chromium, firefox, webkit
    headless: bool = True
    viewport_width: int = 1280
    viewport_height: int = 720
    user_agent: str | None = None
    device: str | None = None  # Playwright device descriptor name (emulation)
    ignore_https_errors: bool = False  # True for localhost
    storage_state: dict[str, Any] | None = None  # For session reuse
    extra_http_headers: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = 30000
    locale: str = "en-US"
    enable_tracing: bool = False  # Enable Playwright trace recording
    trace_dir: str = "traces"  # Directory to save trace .zip files
    job_id: str = ""  # Job ID for trace file naming
    record_video_dir: str | None = None
    record_har_path: str | None = None


class InstrumentedPage:
    """A Playwright page with built-in console capture and request interception.

    Wraps a Playwright Page to automatically capture:
    - All console messages → ConsoleCapture
    - All network requests → EndpointRegistry
    - All page errors → ConsoleCapture (as CRITICAL)
    """

    def __init__(
        self,
        page: Page,
        console_capture: ConsoleCapture,
        endpoint_registry: EndpointRegistry,
        browser_engine: str = "unknown",
        trace_path: str | None = None,
        cdp_session: Any | None = None,
    ) -> None:
        self.page = page
        self.console = console_capture
        self.endpoints = endpoint_registry
        self.browser_engine = browser_engine
        self._trace_path = trace_path  # Path to save trace .zip on close
        self.cdp_session = cdp_session  # CDP session for performance metrics (Chromium only)
        self._setup_listeners()

    def _setup_listeners(self) -> None:
        """Attach console, error, and request listeners to the page."""
        # Console message handler
        self.page.on("console", self._on_console)
        # Page error handler (uncaught exceptions)
        self.page.on("pageerror", self._on_page_error)
        # Request handler (for endpoint discovery)
        self.page.on("request", self._on_request)
        # Response handler (for error detection)
        self.page.on("response", self._on_response)
        # Request-failed handler (for requests that never get a response:
        # connection refused, DNS failures, aborted/blocked, CORS preflight)
        self.page.on("requestfailed", self._on_request_failed)

    def _on_console(self, msg: Any) -> None:
        """Handle console messages."""
        import contextlib

        with contextlib.suppress(Exception):
            self.console.add_entry(
                message=msg.text,
                message_type=msg.type,
                source_location=f"{msg.location.get('url', '')}:{msg.location.get('lineNumber', '')}:{msg.location.get('columnNumber', '')}"
                if hasattr(msg, "location") and msg.location
                else "",
            )

    def _on_page_error(self, error: Any) -> None:
        """Handle page-level errors (uncaught exceptions)."""
        try:
            error_msg = str(error) if error else "Unknown page error"
            self.console.add_page_error(
                error_message=error_msg,
                stack_trace=getattr(error, "stack", "") if hasattr(error, "stack") else "",
            )
        except Exception:
            pass

    def _on_request(self, request: Any) -> None:
        """Handle outbound requests for endpoint discovery."""
        try:
            url = request.url
            method = request.method
            self.endpoints.add(
                url=url,
                method=method,
                source=EndpointSource.NETWORK_CAPTURE,
                discovered_on_page=self.page.url,
            )
        except Exception:
            pass

    def _on_response(self, response: Any) -> None:
        """Handle responses to detect network failures."""
        try:
            status = response.status
            if status >= 400:
                self.console.add_network_failure(
                    url=response.url,
                    method=response.request.method,
                    status_code=status,
                    status_text=response.status_text,
                )
        except Exception:
            pass

    # Resource types that represent a failed *asset* load (broken image,
    # missing CSS/JS/font) rather than a failed API/document request.
    _RESOURCE_FAILURE_TYPES = frozenset({"image", "stylesheet", "font", "media", "script"})

    def _on_request_failed(self, request: Any) -> None:
        """Handle requests that failed without ever receiving a response.

        Covers net::ERR_CONNECTION_REFUSED, DNS failures (ERR_NAME_NOT_RESOLVED),
        aborted/blocked requests, and CORS preflight failures - none of which
        surface via the ``response`` event.
        """
        try:
            error_text = request.failure or "request failed"
            resource_type = getattr(request, "resource_type", "") or ""
            if resource_type in self._RESOURCE_FAILURE_TYPES:
                self.console.add_resource_failure(
                    resource_url=request.url,
                    resource_type=resource_type,
                )
            else:
                self.console.add_network_failure(
                    url=request.url,
                    method=request.method,
                    status_code=0,
                    error_text=error_text,
                )
        except Exception:
            pass

    async def goto(self, url: str, **kwargs: Any) -> Any:
        """Navigate to a URL (delegates to page.goto)."""
        self.console._report.url = url
        return await self.page.goto(url, **kwargs)

    async def get_cdp_performance_metrics(self) -> dict[str, float]:
        """Extract performance metrics via Chrome DevTools Protocol.

        Returns a dict of metric_name -> value. Only works on Chromium.
        Returns empty dict on Firefox/WebKit or if CDP session is unavailable.
        """
        if not self.cdp_session:
            return {}
        try:
            result = await self.cdp_session.send("Performance.getMetrics")
            metrics = {m["name"]: m["value"] for m in result.get("metrics", [])}
            return metrics
        except Exception as e:
            logger.debug(f"CDP Performance.getMetrics failed: {e}")
            return {}

    async def get_navigation_timing(self) -> dict[str, Any]:
        """Extract Navigation Timing API data from the browser.

        Works on all browsers. Returns timing entries for DNS, TCP, TTFB, etc.
        """
        try:
            timing = await self.page.evaluate(
                "JSON.parse(JSON.stringify(performance.getEntriesByType('navigation')[0] || {}))"
            )
            return timing or {}
        except Exception as e:
            logger.debug(f"Navigation Timing extraction failed: {e}")
            return {}

    async def close(self) -> None:
        """Stop tracing, close the page and its context."""
        # Save trace before closing context
        if self._trace_path:
            try:
                await self.page.context.tracing.stop(path=self._trace_path)
                logger.info(f"Trace saved to {self._trace_path}")
            except Exception as e:
                logger.warning(f"Failed to save trace: {e}")
        with contextlib.suppress(Exception):
            await self.page.context.close()
        with contextlib.suppress(Exception):
            await self.page.close()


class BrowserContextFactory:
    """Factory for creating instrumented browser contexts.

    Produces configured contexts for Chromium/Firefox/WebKit with:
    - Automatic console error capture
    - Automatic request interception for endpoint discovery
    - storageState injection for session reuse

    Usage:
        async with BrowserContextFactory() as factory:
            page = await factory.create_page(
                config=BrowserConfig(engine="chromium"),
                endpoint_registry=registry,
            )
            await page.goto("https://example.com")
            report = page.console.finalize()
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browsers: dict[str, Browser] = {}

    async def __aenter__(self) -> BrowserContextFactory:
        self._playwright = await async_playwright().start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        for browser in self._browsers.values():
            await browser.close()
        self._browsers.clear()
        if self._playwright:
            await self._playwright.stop()

    def _get_browser_type(self, engine: str) -> BrowserType:
        """Get the Playwright browser type for the given engine."""
        if not self._playwright:
            raise RuntimeError("Factory not initialized. Use 'async with BrowserContextFactory()' context manager.")

        engines: dict[str, BrowserType] = {
            "chromium": self._playwright.chromium,
            "firefox": self._playwright.firefox,
            "webkit": self._playwright.webkit,
        }

        if engine not in engines:
            raise ValueError(f"Unknown browser engine: {engine}. Must be one of: {list(engines.keys())}")

        return engines[engine]

    async def _get_browser(self, engine: str, headless: bool = True) -> Browser:
        """Get or launch a browser instance."""
        key = f"{engine}:{headless}"
        if key not in self._browsers:
            browser_type = self._get_browser_type(engine)
            launch_kwargs = {"headless": headless}
            if not headless:
                launch_kwargs["slow_mo"] = 1000
            self._browsers[key] = await browser_type.launch(**launch_kwargs)
        return self._browsers[key]

    async def create_context(self, config: BrowserConfig) -> BrowserContext:
        """Create a new browser context with the given configuration."""
        browser = await self._get_browser(config.engine, config.headless)

        context_options: dict[str, Any] = {
            "viewport": {"width": config.viewport_width, "height": config.viewport_height},
            "ignore_https_errors": config.ignore_https_errors,
            "locale": config.locale,
        }

        # Device emulation: a known Playwright device descriptor overrides
        # viewport/user-agent/touch/DPR. Unknown names fall back to explicit opts.
        if config.device:
            descriptor = resolve_device(config.device)
            if descriptor:
                context_options["user_agent"] = descriptor["user_agent"]
                context_options["viewport"] = descriptor["viewport"]
                if descriptor.get("device_scale_factor"):
                    context_options["device_scale_factor"] = descriptor["device_scale_factor"]
                if descriptor.get("is_mobile"):
                    context_options["is_mobile"] = True
                if descriptor.get("has_touch"):
                    context_options["has_touch"] = True

        if config.user_agent and "user_agent" not in context_options:
            context_options["user_agent"] = config.user_agent

        if config.storage_state:
            context_options["storage_state"] = config.storage_state

        if config.extra_http_headers:
            context_options["extra_http_headers"] = config.extra_http_headers

        if config.record_video_dir:
            context_options["record_video_dir"] = config.record_video_dir
            context_options["record_video_size"] = {"width": config.viewport_width, "height": config.viewport_height}

        if config.record_har_path:
            context_options["record_har_path"] = config.record_har_path

        return await browser.new_context(**context_options)

    async def create_page(
        self,
        config: BrowserConfig,
        endpoint_registry: EndpointRegistry | None = None,
        initial_url: str = "",
    ) -> InstrumentedPage:
        """Create a new instrumented page with console capture and request interception.

        This is the primary method — every page created through this factory
        automatically captures console errors and discovers endpoints.
        Optionally starts Playwright tracing and opens a CDP session.
        """
        context = await self.create_context(config)

        # Start tracing if enabled
        trace_path: str | None = None
        if config.enable_tracing:
            try:
                os.makedirs(config.trace_dir, exist_ok=True)
                trace_path = os.path.join(config.trace_dir, f"{config.job_id}_{config.engine}.zip")
                await context.tracing.start(
                    screenshots=True,
                    snapshots=True,
                    sources=True,
                )
                logger.info(f"Tracing started for {config.engine} (will save to {trace_path})")
            except Exception as e:
                logger.warning(f"Failed to start tracing: {e}")
                trace_path = None

        page = await context.new_page()

        # Apply stealth profile to bypass bot detection (Cloudflare, etc.)
        try:
            from playwright_stealth import Stealth

            await Stealth().apply_stealth_async(page)
            logger.info(f"Stealth profile successfully applied on {config.engine}")
        except Exception as e:
            logger.warning(f"Failed to apply stealth profile: {e}")

        # Set default timeout
        page.set_default_timeout(config.timeout_ms)

        # Open CDP session for Chromium (for performance metrics)
        cdp_session = None
        if config.engine == "chromium":
            try:
                cdp_session = await context.new_cdp_session(page)
                await cdp_session.send("Performance.enable")
                logger.info("CDP session opened — Performance.enable sent")
            except Exception as e:
                logger.debug(f"CDP session not available (expected on non-Chromium): {e}")

        # Create console capture for this page
        capture = ConsoleCapture(
            url=initial_url or "about:blank",
            browser=config.engine,
        )

        # Use provided registry or create a new one
        registry = endpoint_registry or EndpointRegistry()

        return InstrumentedPage(
            page=page,
            console_capture=capture,
            endpoint_registry=registry,
            browser_engine=config.engine,
            trace_path=trace_path,
            cdp_session=cdp_session,
        )

    async def create_pages_for_matrix(
        self,
        browser_matrix: dict[str, bool],
        endpoint_registry: EndpointRegistry | None = None,
        headless: bool = True,
        ignore_https_errors: bool = False,
        storage_state: dict[str, Any] | None = None,
        extra_http_headers: dict[str, str] | None = None,
    ) -> dict[str, InstrumentedPage]:
        """Create instrumented pages for all enabled browsers in the matrix.

        Returns a dict mapping engine name → InstrumentedPage.
        """
        pages: dict[str, InstrumentedPage] = {}

        for engine, enabled in browser_matrix.items():
            if not enabled:
                continue

            config = BrowserConfig(
                engine=engine,
                headless=headless,
                ignore_https_errors=ignore_https_errors,
                storage_state=storage_state,
                extra_http_headers=extra_http_headers or {},
            )

            pages[engine] = await self.create_page(
                config=config,
                endpoint_registry=endpoint_registry,
            )

        return pages
