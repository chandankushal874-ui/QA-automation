"""Centralized endpoint registry for discovered endpoints.

This module is the cross-cutting concern for exhaustive endpoint discovery.
Every step that touches the target app feeds discovered endpoints here.

Discovery sources:
- DOM link extraction (a, form, iframe, link, script src tags)
- JS static analysis (regex + AST parsing for hardcoded URLs)
- Network interception (XHR, fetch, WebSocket, EventSource, GraphQL)
- Source map mining (.map files → original file paths and route definitions)
- GraphQL introspection (schema queries → all queries/mutations/subscriptions)
- API brute enumeration (common path wordlist)
- robots.txt / sitemap.xml parsing
- HTML comments / hidden inputs scanning
- OpenAPI/Swagger auto-discovery

After each step, the discovered endpoint list is persisted and diffed.
New endpoints trigger re-crawling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from urllib.parse import unquote, urljoin, urlparse


class EndpointSource(Enum):
    """How the endpoint was discovered."""

    DOM_LINK = "dom_link"  # <a>, <form>, <iframe> tags
    DOM_RESOURCE = "dom_resource"  # <link>, <script src>, <img> tags
    JS_ANALYSIS = "js_analysis"  # Hardcoded URLs found in JS bundles
    NETWORK_CAPTURE = "network_capture"  # Intercepted XHR/fetch/WS requests
    SOURCE_MAP = "source_map"  # Extracted from .map files
    GRAPHQL_INTROSPECTION = "graphql_introspection"  # GraphQL schema queries
    API_BRUTE_FORCE = "api_brute_force"  # Common path wordlist enumeration
    ROBOTS_TXT = "robots_txt"  # From robots.txt
    SITEMAP_XML = "sitemap_xml"  # From sitemap.xml
    HTML_COMMENT = "html_comment"  # URLs found in HTML comments
    HIDDEN_INPUT = "hidden_input"  # URLs found in hidden form inputs
    OPENAPI_SPEC = "openapi_spec"  # From OpenAPI/Swagger spec files
    USER_PROVIDED = "user_provided"  # Explicitly provided by the user
    REDIRECT = "redirect"  # Discovered via redirect chain
    HEADER_LINK = "header_link"  # From Link headers in HTTP responses


class EndpointStatus(Enum):
    """Current status of a discovered endpoint."""

    DISCOVERED = "discovered"  # Found but not yet visited
    QUEUED = "queued"  # In the crawl queue
    VISITED = "visited"  # Successfully visited
    FAILED = "failed"  # Visit resulted in error
    SKIPPED = "skipped"  # Intentionally skipped (budget, scope, etc.)
    BLOCKED = "blocked"  # Blocked by auth, robots.txt, etc.


class EndpointType(Enum):
    """Type classification for discovered endpoints."""

    PAGE = "page"  # Standard web page
    API = "api"  # API endpoint
    GRAPHQL = "graphql"  # GraphQL endpoint
    WEBSOCKET = "websocket"  # WebSocket endpoint
    STATIC_ASSET = "static_asset"  # CSS, JS, image, font
    DOWNLOAD = "download"  # File download
    AUTH = "auth"  # Login/logout/auth endpoint
    ADMIN = "admin"  # Admin panel
    DEBUG = "debug"  # Debug/diagnostic endpoint
    SPEC = "spec"  # OpenAPI spec, swagger.json
    UNKNOWN = "unknown"


# Common API/admin/debug paths for brute-force discovery
COMMON_PATHS: list[str] = [
    "/api",
    "/api/v1",
    "/api/v2",
    "/api/v3",
    "/graphql",
    "/graphiql",
    "/playground",
    "/admin",
    "/admin/",
    "/administrator",
    "/internal/admin",
    "/dashboard",
    "/console",
    "/debug",
    "/debug/",
    "/_debug",
    "/health",
    "/healthz",
    "/health/check",
    "/status",
    "/.env",
    "/.env.local",
    "/.env.production",
    "/swagger.json",
    "/openapi.json",
    "/api-docs",
    "/swagger",
    "/docs",
    "/redoc",
    "/.git/config",
    "/.git/HEAD",
    "/robots.txt",
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/wp-admin",
    "/wp-login.php",  # WordPress
    "/.well-known/security.txt",
    "/favicon.ico",
    "/config",
    "/configuration",
    "/settings",
    "/login",
    "/signin",
    "/signup",
    "/register",
    "/logout",
    "/auth",
    "/oauth",
    "/callback",
    "/token",
    "/metrics",
    "/prometheus",
    "/actuator",
    "/actuator/health",
    "/actuator/info",  # Spring Boot
    "/__nextjs_original-stack-frame",  # Next.js debug
    "/_next/data",  # Next.js data
    "/server-info",
    "/phpinfo.php",
]


# Expected pages that should exist on a well-formed website.
# Used by the sitemap/expected-page verification pass to flag
# missing pages (404s) as FUNCTIONAL issues — mirrors the
# DeepSeek report's "expected pages missing" detection.
EXPECTED_PAGES: list[str] = [
    "/login",
    "/signin",
    "/signup",
    "/register",
    "/logout",
    "/dashboard",
    "/account",
    "/profile",
    "/settings",
    "/pricing",
    "/plans",
    "/billing",
    "/about",
    "/about-us",
    "/contact",
    "/contact-us",
    "/support",
    "/help",
    "/faq",
    "/faqs",
    "/terms",
    "/terms-and-conditions",
    "/terms-of-service",
    "/privacy",
    "/privacy-policy",
    "/gallery",
    "/reviews",
    "/testimonials",
    "/syllabus",
    "/curriculum",
    "/courses",
    "/blog",
    "/news",
    "/resources",
    "/sitemap.xml",
    "/robots.txt",
    "/llms.txt",
    "/sitemap_index.xml",
    "/.well-known/security.txt",
    "/ads.txt",  # Google Ads verification
    "/humans.txt",  # Human-readable site info
]


@dataclass
class DiscoveredEndpoint:
    """A single discovered endpoint with metadata."""

    url: str  # Full URL
    method: str = "GET"  # HTTP method
    endpoint_type: EndpointType = EndpointType.UNKNOWN
    source: EndpointSource = EndpointSource.DOM_LINK
    status: EndpointStatus = EndpointStatus.DISCOVERED
    discovered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    discovered_on_page: str = ""  # The page where this endpoint was found
    visit_count: int = 0
    last_status_code: int | None = None
    last_visited_at: datetime | None = None
    requires_auth: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def path(self) -> str:
        """Extract the path component from the URL."""
        return urlparse(self.url).path

    @property
    def domain(self) -> str:
        """Extract the domain from the URL."""
        parsed = urlparse(self.url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def fingerprint(self) -> str:
        """Generate a dedup fingerprint for this endpoint.

        Normalizes numeric/UUID path segments to collapse similar endpoints.
        Decodes percent-encoded characters in the path so that
        ``/model/a%7Cb`` and ``/model/a|b`` produce the same fingerprint.
        """
        # Decode percent-encoded chars in path FIRST (e.g. %7C → |, %20 → space)
        # so the same page discovered via different encodings dedupes correctly.
        path = unquote(urlparse(self.url).path)
        # Collapse UUIDs FIRST (before numeric, since UUID segments contain numbers)
        path = re.sub(
            r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "/{uuid}",
            path,
            flags=re.IGNORECASE,
        )
        # Then collapse numeric segments: /users/123 → /users/{id}
        path = re.sub(r"/\d+", "/{id}", path)

        # Keep query/mutation/subscription parameter in fingerprint for GraphQL virtual endpoints
        parsed = urlparse(self.url)
        query = parsed.query
        if query:
            match = re.search(r"\b(query|mutation|subscription)=([a-zA-Z0-9_]+)", query)
            if match:
                path = f"{path}?{match.group(1)}={match.group(2)}"

        return f"{self.method}::{self.domain}{path}"


def classify_endpoint_type(url: str, method: str = "GET") -> EndpointType:
    """Infer the endpoint type from its URL pattern.

    Args:
        url: The endpoint URL.
        method: The HTTP method.

    Returns:
        Best-guess EndpointType.
    """
    path = urlparse(url).path.lower()

    if any(path.startswith(p) for p in ("/api", "/_api", "/v1/", "/v2/", "/v3/")):
        return EndpointType.API

    if "graphql" in path or "graphiql" in path or "playground" in path:
        return EndpointType.GRAPHQL

    if url.startswith("ws://") or url.startswith("wss://"):
        return EndpointType.WEBSOCKET

    if any(path.startswith(p) for p in ("/admin", "/internal/admin", "/administrator", "/dashboard")):
        return EndpointType.ADMIN

    if any(
        path == f"/{p}" or path.startswith(f"/{p}/") or path == f"/_{p}" or path.startswith(f"/_{p}/")
        for p in ("debug", "actuator", "metrics", "console")
    ):
        return EndpointType.DEBUG

    if any(path.endswith(ext) for ext in (".json", ".yaml", ".yml")) and any(
        kw in path for kw in ("swagger", "openapi", "api-docs")
    ):
        return EndpointType.SPEC

    if any(path.startswith(p) for p in ("/login", "/signin", "/signup", "/auth", "/oauth", "/logout")):
        return EndpointType.AUTH

    if any(path.endswith(ext) for ext in (".css", ".js", ".png", ".jpg", ".gif", ".svg", ".woff", ".woff2", ".ico")):
        return EndpointType.STATIC_ASSET

    if any(path.endswith(ext) for ext in (".pdf", ".zip", ".tar", ".gz", ".csv", ".xlsx")):
        return EndpointType.DOWNLOAD

    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return EndpointType.API

    return EndpointType.PAGE


class EndpointRegistry:
    """Centralized store for all discovered endpoints.

    Provides deduplication, diffing between discovery rounds,
    and retrieval by various criteria.
    """

    def __init__(self, base_url: str = "", scope: str = "same-domain") -> None:
        self._base_url = base_url
        self._scope = scope
        self._endpoints: dict[str, DiscoveredEndpoint] = {}  # keyed by fingerprint
        self._discovery_round: int = 0
        self._round_snapshots: dict[int, set[str]] = {}  # round → set of fingerprints

    def _is_same_scope(self, url: str) -> bool:
        """Helper to validate if a URL is in-scope compared to base_url."""
        if not self._base_url or self._scope == "all":
            return True

        parsed_url = urlparse(url)
        parsed_base = urlparse(self._base_url)

        if self._scope == "same-origin":
            return parsed_url.scheme == parsed_base.scheme and parsed_url.netloc == parsed_base.netloc

        if self._scope == "same-domain":

            def get_domain(netloc: str) -> str:
                host = netloc.split(":")[0]
                parts = host.split(".")
                if len(parts) >= 2:
                    return ".".join(parts[-2:])
                return host

            return get_domain(parsed_url.netloc) == get_domain(parsed_base.netloc)

        return True

    def _is_noise_domain(self, url: str) -> bool:
        """Filter out common tracking, CDN, and telemetry domains to prevent crawler traps."""
        parsed = urlparse(url)
        host = parsed.netloc.lower()

        noise_patterns = [
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
            "browser-update",
            "googlefonts",
            "googleapis",
            "gstatic",
            "cloudflare",
            "webpack",
            "hmr",
            "sockjs",
            "livereload",
            "telemetry",
        ]

        return any(pattern in host for pattern in noise_patterns)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def total_count(self) -> int:
        return len(self._endpoints)

    @property
    def current_round(self) -> int:
        return self._discovery_round

    def add(
        self,
        url: str,
        method: str = "GET",
        source: EndpointSource = EndpointSource.DOM_LINK,
        discovered_on_page: str = "",
        requires_auth: bool = False,
        headers: dict[str, str] | None = None,
        extra: dict[str, str] | None = None,
    ) -> tuple[DiscoveredEndpoint | None, bool]:
        """Add a discovered endpoint. Returns (endpoint, is_new).

        Deduplicates by fingerprint. If the endpoint already exists,
        updates metadata but returns is_new=False.
        Filters out out-of-scope and noise/telemetry URLs.
        """
        # Resolve relative URLs
        if self._base_url and not url.startswith(("http://", "https://", "ws://", "wss://")):
            url = urljoin(self._base_url, url)

        # 1. Skip data and blob URIs
        if url.lower().startswith(("data:", "blob:", "mailto:", "tel:")):
            return None, False

        # 2. Skip telemetry/tracking/CDN noise domains
        if self._is_noise_domain(url):
            return None, False

        # 3. Apply domain/origin scope filter
        if not self._is_same_scope(url):
            return None, False

        endpoint_type = classify_endpoint_type(url, method)
        endpoint = DiscoveredEndpoint(
            url=url,
            method=method,
            endpoint_type=endpoint_type,
            source=source,
            discovered_on_page=discovered_on_page,
            requires_auth=requires_auth,
            headers=headers or {},
            extra=extra or {},
        )

        fp = endpoint.fingerprint()
        is_new = fp not in self._endpoints

        if is_new:
            self._endpoints[fp] = endpoint
        else:
            # Update discovery source if this is a new source for an existing endpoint
            existing = self._endpoints[fp]
            if existing.source != source:
                existing.extra[f"also_found_via_{source.value}"] = discovered_on_page

        return self._endpoints[fp], is_new

    def add_many(
        self,
        urls: list[str],
        method: str = "GET",
        source: EndpointSource = EndpointSource.DOM_LINK,
        discovered_on_page: str = "",
    ) -> list[DiscoveredEndpoint]:
        """Add multiple endpoints at once. Returns only newly discovered ones."""
        new_endpoints = []
        for url in urls:
            endpoint, is_new = self.add(
                url=url,
                method=method,
                source=source,
                discovered_on_page=discovered_on_page,
            )
            if is_new:
                new_endpoints.append(endpoint)
        return new_endpoints

    def get(self, url: str, method: str = "GET") -> DiscoveredEndpoint | None:
        """O(1) lookup of a single endpoint by URL + method (fingerprint-keyed)."""
        fp = DiscoveredEndpoint(url=url, method=method).fingerprint()
        return self._endpoints.get(fp)

    def get_all(self) -> list[DiscoveredEndpoint]:
        """Return all discovered endpoints."""
        return list(self._endpoints.values())

    def get_by_status(self, status: EndpointStatus) -> list[DiscoveredEndpoint]:
        """Return endpoints filtered by status."""
        return [ep for ep in self._endpoints.values() if ep.status == status]

    def get_by_type(self, endpoint_type: EndpointType) -> list[DiscoveredEndpoint]:
        """Return endpoints filtered by type."""
        return [ep for ep in self._endpoints.values() if ep.endpoint_type == endpoint_type]

    def get_by_source(self, source: EndpointSource) -> list[DiscoveredEndpoint]:
        """Return endpoints filtered by discovery source."""
        return [ep for ep in self._endpoints.values() if ep.source == source]

    def get_unvisited(self) -> list[DiscoveredEndpoint]:
        """Return endpoints that haven't been visited yet."""
        return [
            ep for ep in self._endpoints.values() if ep.status in (EndpointStatus.DISCOVERED, EndpointStatus.QUEUED)
        ]

    def get_requiring_auth(self) -> list[DiscoveredEndpoint]:
        """Return endpoints that require authentication."""
        return [ep for ep in self._endpoints.values() if ep.requires_auth]

    def mark_visited(self, url: str, method: str = "GET", status_code: int | None = None) -> None:
        """Mark an endpoint as visited with the response status code.

        When ``status_code`` is omitted, the status observed during page
        navigation (recorded on the endpoint by the crawler) is preserved;
        only when nothing was ever observed do we assume 200.
        """
        fp = DiscoveredEndpoint(url=url, method=method).fingerprint()
        if fp in self._endpoints:
            ep = self._endpoints[fp]
            ep.status = EndpointStatus.VISITED
            ep.visit_count += 1
            if status_code is not None:
                ep.last_status_code = status_code
            elif ep.last_status_code is None:
                ep.last_status_code = 200
            ep.last_visited_at = datetime.now(UTC)

    def mark_failed(self, url: str, method: str = "GET", status_code: int = 0) -> None:
        """Mark an endpoint as failed."""
        fp = DiscoveredEndpoint(url=url, method=method).fingerprint()
        if fp in self._endpoints:
            ep = self._endpoints[fp]
            ep.status = EndpointStatus.FAILED
            ep.last_status_code = status_code
            ep.last_visited_at = datetime.now(UTC)

    def start_new_round(self) -> int:
        """Start a new discovery round. Returns the round number.

        Used to diff endpoints discovered between rounds —
        new endpoints found after a round trigger re-crawling.
        """
        self._discovery_round += 1
        self._round_snapshots[self._discovery_round] = set(self._endpoints.keys())
        return self._discovery_round

    def get_new_since_round(self, round_number: int) -> list[DiscoveredEndpoint]:
        """Return endpoints discovered after the given round.

        Used to identify newly found endpoints that should trigger re-crawling.
        """
        if round_number not in self._round_snapshots:
            return self.get_all()

        previous_fps = self._round_snapshots[round_number]
        return [ep for fp, ep in self._endpoints.items() if fp not in previous_fps]

    def summary(self) -> dict[str, int]:
        """Return a summary of endpoint counts by type and status."""
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}

        for ep in self._endpoints.values():
            by_type[ep.endpoint_type.value] = by_type.get(ep.endpoint_type.value, 0) + 1
            by_status[ep.status.value] = by_status.get(ep.status.value, 0) + 1

        return {
            "total": len(self._endpoints),
            "by_type": by_type,
            "by_status": by_status,
            "discovery_round": self._discovery_round,
        }

    def generate_brute_force_urls(self, base_url: str | None = None) -> list[str]:
        """Generate common path URLs for brute-force endpoint discovery.

        Returns full URLs for all common paths that haven't been discovered yet.
        """
        base = base_url or self._base_url
        if not base:
            return []

        # Strip trailing slash from base
        base = base.rstrip("/")

        existing_paths = {ep.path for ep in self._endpoints.values()}
        urls = []
        for path in COMMON_PATHS:
            if path not in existing_paths:
                urls.append(f"{base}{path}")
        return urls
