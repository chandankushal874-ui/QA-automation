"""Independent API endpoint testing engine.

After the browser crawl discovers API endpoints via network interception,
this module independently tests each discovered API to validate:
- Response status codes
- Response times
- JSON schema validity
- Content-Type headers
- CORS headers presence

No browser needed — uses httpx for direct HTTP requests.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from src.core.endpoint_registry import EndpointRegistry, EndpointSource

logger = logging.getLogger(__name__)

# Default timeout for API requests (seconds)
API_TIMEOUT = 10.0

# Slow API threshold (milliseconds)
SLOW_API_THRESHOLD_MS = 3000


@dataclass
class APITestResult:
    """Result of testing a single API endpoint."""

    url: str
    method: str = "GET"
    status_code: int | None = None
    response_time_ms: float = 0.0
    content_type: str = ""
    is_json_valid: bool = True
    has_cors_headers: bool = False
    is_slow: bool = False
    error_message: str = ""
    response_body_preview: str = ""  # First 200 chars of body


@dataclass
class APITestSummary:
    """Summary of all API endpoint tests."""

    total_tested: int = 0
    total_passed: int = 0
    total_failed: int = 0
    total_slow: int = 0
    results: list[APITestResult] = field(default_factory=list)


class APITester:
    """Tests discovered API endpoints independently of the browser.

    Usage:
        tester = APITester()
        summary = await tester.test_discovered_apis(registry, base_url="https://example.com")
    """

    def __init__(self, timeout: float = API_TIMEOUT) -> None:
        self.timeout = timeout

    async def test_single_api(self, url: str, method: str = "GET") -> APITestResult:
        """Test a single API endpoint and return the result."""
        result = APITestResult(url=url, method=method)

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
                verify=False,  # Allow self-signed certs for localhost
            ) as client:
                start_time = time.monotonic()

                if method.upper() == "GET":
                    response = await client.get(url)
                elif method.upper() == "POST":
                    response = await client.post(url, json={})
                elif method.upper() == "PUT":
                    response = await client.put(url, json={})
                elif method.upper() == "DELETE":
                    response = await client.delete(url)
                elif method.upper() == "PATCH":
                    response = await client.patch(url, json={})
                elif method.upper() == "HEAD":
                    response = await client.head(url)
                elif method.upper() == "OPTIONS":
                    response = await client.options(url)
                else:
                    response = await client.get(url)

                elapsed_ms = (time.monotonic() - start_time) * 1000

                result.status_code = response.status_code
                result.response_time_ms = elapsed_ms
                result.content_type = response.headers.get("content-type", "")
                result.is_slow = elapsed_ms > SLOW_API_THRESHOLD_MS

                # Check CORS headers
                cors_headers = [
                    "access-control-allow-origin",
                    "access-control-allow-methods",
                    "access-control-allow-headers",
                ]
                result.has_cors_headers = any(h in response.headers for h in cors_headers)

                # Validate JSON if content-type is JSON
                if "application/json" in result.content_type:
                    try:
                        response.json()
                        result.is_json_valid = True
                    except Exception:
                        result.is_json_valid = False
                        result.error_message = "Invalid JSON response body"

                # Preview body
                body_text = response.text[:200] if response.text else ""
                result.response_body_preview = body_text

                logger.info(f"API Test: {method} {url} → {result.status_code} ({elapsed_ms:.0f}ms)")

        except httpx.TimeoutException:
            result.error_message = f"Timeout after {self.timeout}s"
            result.is_slow = True
            logger.warning(f"API Timeout: {method} {url}")
        except httpx.ConnectError as e:
            result.error_message = f"Connection failed: {e}"
            logger.warning(f"API Connection failed: {method} {url}")
        except Exception as e:
            result.error_message = str(e)
            logger.error(f"API Test error: {method} {url}: {e}")

        return result

    async def test_discovered_apis(
        self,
        registry: EndpointRegistry,
        base_url: str = "",
    ) -> APITestSummary:
        """Test all API endpoints discovered by network interception.

        Filters the registry for endpoints discovered via NETWORK_CAPTURE
        that look like API calls (not static assets, not HTML pages).
        """
        summary = APITestSummary()

        all_endpoints = registry.get_all()

        # Filter for API-like endpoints
        api_endpoints = []
        for ep in all_endpoints:
            # Only test network-captured endpoints
            if ep.source != EndpointSource.NETWORK_CAPTURE:
                continue

            url_lower = ep.url.lower()

            # Skip static assets
            static_extensions = (
                ".js",
                ".css",
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".ico",
                ".svg",
                ".woff",
                ".woff2",
                ".ttf",
                ".eot",
                ".map",
                ".pdf",
                ".zip",
                ".mp4",
                ".webm",
                ".webp",
            )
            if any(url_lower.endswith(ext) for ext in static_extensions):
                continue

            # Skip data URIs and blob URLs
            if url_lower.startswith("data:") or url_lower.startswith("blob:"):
                continue

            # Skip third-party tracking/analytics
            tracking_domains = (
                "google-analytics",
                "googletagmanager",
                "facebook",
                "doubleclick",
                "adsense",
                "hotjar",
                "segment",
                "mixpanel",
                "amplitude",
                "sentry",
                "datadog",
            )
            if any(domain in url_lower for domain in tracking_domains):
                continue

            # Must be from the same origin or an /api/ path
            from urllib.parse import urlparse

            parsed = urlparse(ep.url)
            base_parsed = urlparse(base_url) if base_url else None

            is_same_origin = (base_parsed and parsed.netloc == base_parsed.netloc) if base_parsed else True

            is_api_path = "/api/" in url_lower or "/graphql" in url_lower

            if is_same_origin or is_api_path:
                api_endpoints.append(ep)

        logger.info(f"Found {len(api_endpoints)} API endpoints to test independently")

        # Test each API endpoint
        for ep in api_endpoints:
            result = await self.test_single_api(ep.url, ep.method)
            summary.results.append(result)
            summary.total_tested += 1

            if result.status_code and 200 <= result.status_code < 400:
                summary.total_passed += 1
            else:
                summary.total_failed += 1

            if result.is_slow:
                summary.total_slow += 1

        logger.info(
            f"API Testing complete: {summary.total_tested} tested, "
            f"{summary.total_passed} passed, {summary.total_failed} failed, "
            f"{summary.total_slow} slow"
        )

        return summary
