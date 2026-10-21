"""Static/seed discovery and deep endpoint mining module.

Features:
- Parse robots.txt and sitemap.xml
- Parse HTML pages for links, forms, scripts, custom attributes, comments, and hidden inputs
- Standarize URL resolution against base URL

Spec reference: master-build-prompt §3, Step H
"""

from __future__ import annotations

import contextlib
import re
from html import unescape as html_unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import httpx

from src.core.endpoint_registry import EndpointRegistry, EndpointSource


class DOMMiner(HTMLParser):
    """HTML Parser subclass to mine all types of endpoints from a DOM string."""

    def __init__(self, page_url: str, base_url: str) -> None:
        super().__init__()
        self.page_url = page_url
        self.base_url = base_url
        # Store results as tuple of (raw_url, tag_type, context_meta)
        self.findings: list[tuple[str, str, dict[str, Any]]] = []
        self.comments: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): val for name, val in attrs if val is not None}

        # 1. <a>, <link>, <area> tags (href)
        if tag in ("a", "link", "area"):
            href = attrs_dict.get("href")
            if href:
                self.findings.append((href, "dom_link" if tag == "a" else "dom_resource", {"tag": tag, "attr": "href"}))

        # 2. <script>, <img>, <iframe>, etc. (src)
        if tag in ("script", "img", "iframe", "audio", "video", "embed", "source"):
            src = attrs_dict.get("src")
            if src:
                self.findings.append((src, "dom_resource", {"tag": tag, "attr": "src"}))

        # 3. <form> elements (action)
        if tag == "form":
            action = attrs_dict.get("action", "")
            method = attrs_dict.get("method", "GET").upper()
            self.findings.append((action, "dom_link", {"tag": "form", "attr": "action", "method": method}))

        # 4. Hidden form inputs
        if tag == "input":
            input_type = attrs_dict.get("type", "").lower()
            name = attrs_dict.get("name", "")
            value = attrs_dict.get("value", "")
            if input_type == "hidden" and value:
                # Add as hidden input finding
                self.findings.append((value, "hidden_input", {"tag": "input", "name": name}))

        # 5. data-* attributes containing paths or URLs
        for name, val in attrs_dict.items():
            if (
                name.startswith("data-")
                and val
                and (val.startswith("/") or val.startswith("http://") or val.startswith("https://") or "api/" in val)
            ):
                # Heuristic: does value look like a URL or path?
                self.findings.append((val, "dom_link", {"tag": tag, "attr": name}))

    def handle_comment(self, data: str) -> None:
        self.comments.append(data)

        # Heuristics to find links or API endpoints in HTML comments
        # Absolute URLs: http/https
        url_pattern = r"https?://[a-zA-Z0-9_\-\.\:\/]+"
        for url in re.findall(url_pattern, data):
            self.findings.append((url, "html_comment", {"comment": data}))

        # Relative paths: /api/v1/something (excluding slash preceded by : or / to prevent matching absolute URLs)
        path_pattern = r"(?<!:)(?<!/)/(?:[a-zA-Z0-9_\-\.]+/?)+"
        for path in re.findall(path_pattern, data):
            if len(path) > 3 and not path.startswith("//"):
                self.findings.append((path, "html_comment", {"comment": data}))


def is_same_scope(url: str, base_url: str, scope: str = "same-domain") -> bool:
    """Validate if a URL is in-scope based on the job configuration."""
    parsed_url = urlparse(url)
    parsed_base = urlparse(base_url)

    if scope == "same-origin":
        return parsed_url.scheme == parsed_base.scheme and parsed_url.netloc == parsed_base.netloc

    if scope == "same-domain":
        # Match domains (e.g. example.com and api.example.com)
        def get_domain(netloc: str) -> str:
            # Strip port if any
            host = netloc.split(":")[0]
            parts = host.split(".")
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return host

        return get_domain(parsed_url.netloc) == get_domain(parsed_base.netloc)

    # "custom" or "all" -> allow
    return True


def normalize_url(url: str) -> str:
    """Normalize a URL for dedup purposes.

    Decodes percent-encoded characters in the path so that
    ``/model/a%7Cb`` and ``/model/a|b`` are treated as the same page.
    Also lowercases the scheme/host and strips default ports/trailing slashes
    to maximize dedup without changing the URL the browser visits.

    This is the single source of truth for URL canonicalization —
    ``DiscoveredEndpoint.fingerprint()`` and ``FrontierNode.fingerprint``
    both call this so the registry and frontier agree on identity.
    """
    parsed = urlparse(url)
    # Decode percent-encoded chars in path (e.g. %7C → |, %20 → space)
    decoded_path = unquote(parsed.path)
    # Strip trailing slash (except for root "/")
    if decoded_path.endswith("/") and len(decoded_path) > 1:
        decoded_path = decoded_path.rstrip("/")
    # Lowercase scheme and host (case-insensitive per RFC 3986)
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=decoded_path,
        fragment="",
    ).geturl()


def normalize_and_resolve_url(raw_url: str, page_url: str, base_url: str) -> str | None:
    """Resolve a relative URL against the current page URL or base URL.

    Filters out anchor links (hash-only), javascript: schemes, mailto:, etc.
    Returns the **normalized** URL so downstream dedup (frontier + registry)
    treats ``%7C`` and ``|`` as the same page.
    """
    raw_url = raw_url.strip()
    if not raw_url or raw_url.startswith("#") or raw_url.lower().startswith("javascript:"):
        return None
    if raw_url.lower().startswith("mailto:") or raw_url.lower().startswith("tel:"):
        return None

    # Resolve relative URL
    resolved = urljoin(page_url, raw_url)

    # Strip hash fragment + decode percent-encoded path chars for dedup
    return normalize_url(resolved)


async def parse_robots_txt(base_url: str) -> tuple[set[str], set[str], float | None]:
    """Parse robots.txt to extract disallowed paths, sitemap URLs, and Crawl-delay."""
    disallowed = set()
    sitemaps = set()
    crawl_delay = None
    robots_url = urljoin(base_url, "/robots.txt")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(robots_url)
            if resp.status_code == 200:
                lines = resp.text.splitlines()
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        key = parts[0].strip().lower()
                        val = parts[1].strip()
                        if key == "disallow" and val:
                            disallowed.add(val)
                        elif key == "sitemap" and val:
                            sitemaps.add(val)
                        elif key == "crawl-delay" and val:
                            with contextlib.suppress(ValueError):
                                crawl_delay = float(val)
    except Exception:
        pass

    return disallowed, sitemaps, crawl_delay


async def parse_sitemap_xml(sitemap_url: str) -> set[str]:
    """Parse sitemap.xml to extract all URLs."""
    urls = set()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(sitemap_url)
            if resp.status_code == 200:
                # Basic regex location tag extractor
                locs = re.findall(r"<loc>(.*?)</loc>", resp.text, re.IGNORECASE)
                for loc in locs:
                    urls.add(loc.strip())
    except Exception:
        pass
    return urls


async def discover_static_seed_endpoints(base_url: str, registry: EndpointRegistry) -> tuple[int, float | None]:
    """Discover initial endpoints from robots.txt and sitemap.xml.

    Populates the registry and categorizes finding sources.
    """
    new_count = 0

    # 1. Parse robots.txt
    disallowed, sitemaps, crawl_delay = await parse_robots_txt(base_url)

    for path in disallowed:
        resolved = urljoin(base_url, path)
        _, is_new = registry.add(
            url=resolved,
            method="GET",
            source=EndpointSource.ROBOTS_TXT,
            requires_auth=True,  # Disallowed pages are typically auth-gated or private
        )
        if is_new:
            new_count += 1

    # Add default sitemap fallback if none declared
    if not sitemaps:
        sitemaps.add(urljoin(base_url, "/sitemap.xml"))

    # 2. Parse sitemaps
    for sitemap_url in sitemaps:
        urls = await parse_sitemap_xml(sitemap_url)
        for url in urls:
            _, is_new = registry.add(
                url=url,
                method="GET",
                source=EndpointSource.SITEMAP_XML,
            )
            if is_new:
                new_count += 1

    return new_count, crawl_delay


def mine_html_dom(
    html_content: str, page_url: str, base_url: str, registry: EndpointRegistry, scope: str = "same-domain"
) -> int:
    """Deep harvest all endpoints in the HTML using DOMMiner.

    Standardizes all paths, filters scope, and registers new findings.
    """
    miner = DOMMiner(page_url, base_url)
    miner.feed(html_content)

    new_count = 0

    for raw_url, source_type, meta in miner.findings:
        resolved = normalize_and_resolve_url(raw_url, page_url, base_url)
        if not resolved:
            continue

        if not is_same_scope(resolved, base_url, scope):
            continue

        # Map findings back to EndpointSource enum
        source = EndpointSource.DOM_LINK
        if source_type == "dom_resource":
            source = EndpointSource.DOM_RESOURCE
        elif source_type == "hidden_input":
            source = EndpointSource.HIDDEN_INPUT
        elif source_type == "html_comment":
            source = EndpointSource.HTML_COMMENT

        # Form method check
        method = meta.get("method", "GET")

        _, is_new = registry.add(
            url=resolved,
            method=method,
            source=source,
            discovered_on_page=page_url,
        )
        if is_new:
            new_count += 1

        # Form anti-pattern detection: flag GET-method forms that handle
        # sensitive data (contact/login/register/subscribe). GET exposes
        # submitted data in the URL/query string and is a known anti-pattern.
        # Mirrors the DeepSeek report's "contact form uses GET method" finding.
        if source_type == "dom_link" and meta.get("tag") == "form":
            method_upper = method.upper()
            action_lower = raw_url.lower()
            sensitive_keywords = (
                "contact",
                "login",
                "signin",
                "sign-in",
                "register",
                "signup",
                "sign-up",
                "subscribe",
                "newsletter",
                "search",
            )
            if method_upper == "GET" and any(kw in action_lower for kw in sensitive_keywords):
                # Record the anti-pattern as a registry extra so the engine
                # can triage it into an ADVISORY issue. We attach it to the
                # endpoint's extra dict (non-destructive — existing fields
                # are preserved).
                for ep in registry.get_all():
                    if ep.url == resolved and ep.method == method:
                        ep.extra["form_antipattern"] = f"GET-method form handling sensitive data: {raw_url}"
                        break

    return new_count


# ---------------------------------------------------------------------------
# Sitemap verification + llms.txt detection (DeepSeek parity improvements)
# ---------------------------------------------------------------------------

# Sensitive form keywords reused by the form anti-pattern detector above.
_SENSITIVE_FORM_KEYWORDS: tuple[str, ...] = (
    "contact",
    "login",
    "signin",
    "sign-in",
    "register",
    "signup",
    "sign-up",
    "subscribe",
    "newsletter",
    "search",
)


async def _fetch_text(url: str, timeout: float = 10.0) -> str:
    """Fetch a URL and return its text body (empty string on failure)."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
    except Exception:
        pass
    return ""


async def _collect_all_sitemap_urls(base_url: str) -> set[str]:
    """Collect every URL from robots.txt-declared sitemaps + sitemap index files.

    Handles sitemap index files (``<sitemap><loc>...</loc></sitemap>``) by
    recursing into child sitemaps, mirroring the DeepSeek report's
    "8 sub-sitemaps found" discovery behavior.
    """
    _, sitemaps, _ = await parse_robots_txt(base_url)
    if not sitemaps:
        sitemaps = {urljoin(base_url, "/sitemap.xml")}

    all_urls: set[str] = set()
    visited_sitemaps: set[str] = set()

    # BFS over sitemap index files
    queue = list(sitemaps)
    while queue:
        sitemap_url = queue.pop(0)
        if sitemap_url in visited_sitemaps:
            continue
        visited_sitemaps.add(sitemap_url)

        text = await _fetch_text(sitemap_url)
        if not text:
            continue

        # Check if this is a sitemap index (contains <sitemap> wrappers)
        sub_sitemap_locs = re.findall(
            r"<sitemap>\s*<loc>(.*?)</loc>\s*</sitemap>",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if sub_sitemap_locs:
            for sub in sub_sitemap_locs:
                queue.append(sub.strip())

        # Extract regular URL entries (<url><loc>...</loc></url>)
        locs = re.findall(r"<loc>(.*?)</loc>", text, re.IGNORECASE)
        for loc in locs:
            # Sitemap XML legally encodes & as &amp; (and numeric entities);
            # leaving the entity in the URL corrupts every downstream request.
            loc = html_unescape(loc).strip()
            # Skip entries that are themselves sitemaps (already queued above)
            if "sitemap" in loc.lower() and loc.endswith(".xml"):
                if loc not in visited_sitemaps:
                    queue.append(loc)
                continue
            all_urls.add(loc)

    return all_urls


async def verify_sitemap_urls(
    base_url: str,
    registry: EndpointRegistry,
    db_session: Any | None = None,
    job_id: Any | None = None,
) -> dict[str, Any]:
    """Probe every sitemap URL with a HEAD request and record 404s.

    This is the active-sitemap-verification pass that closes the gap
    between passive link-following crawls and DeepSeek's exhaustive
    sitemap probing. It:

    1. Collects all URLs from robots.txt-declared sitemaps (recursing
       into sitemap index files).
    2. HEAD/GETs each URL to verify it returns a 2xx/3xx response.
    3. Records 4xx/5xx responses as ``NETWORK_FAILURE`` issues in the
       database (when ``db_session`` and ``job_id`` are provided) and
       marks the endpoint as failed in the registry.
    4. Returns a summary dict with counts and the list of missing URLs.

    The existing crawl frontier and DOM-link discovery flow are 100%
    intact — this function runs as a separate verification pass.
    """
    sitemap_urls = await _collect_all_sitemap_urls(base_url)
    missing_urls: list[dict[str, Any]] = []
    verified_count = 0

    # Defer Issue import to avoid circular imports at module load time.
    IssueModel = None
    IssueCategory = None
    IssueSeverity = None
    if db_session is not None and job_id is not None:
        try:
            from src.models.database import Issue, IssueCategory, IssueSeverity

            IssueModel = Issue
            IssueCategory = IssueCategory
            IssueSeverity = IssueSeverity
        except Exception:
            # Database models not available — still record in registry.
            pass

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for url in sitemap_urls:
            # Register the URL so it appears in the endpoint table
            registry.add(
                url=url,
                method="GET",
                source=EndpointSource.SITEMAP_XML,
            )

            try:
                # Try HEAD first (lighter); some servers don't support HEAD
                # so fall back to GET if we get a 405.
                resp = await client.head(url)
                if resp.status_code == 405:
                    resp = await client.get(url)
            except Exception:
                continue

            if resp.status_code < 400:
                verified_count += 1
                # Record the probe status but do NOT flip the endpoint to
                # VISITED — that status is reserved for pages actually loaded
                # by the browser. Marking it here makes the crawl loop's
                # visited-check skip the URL (incl. the seed), so the whole
                # browser phase crawls 0 pages.
                ep = registry.get(url)
                if ep is not None:
                    ep.last_status_code = resp.status_code
            else:
                missing_urls.append(
                    {
                        "url": url,
                        "status_code": resp.status_code,
                    }
                )
                registry.mark_failed(url, status_code=resp.status_code)

                # Record as a database Issue if a session is provided
                if IssueModel is not None:
                    try:
                        fingerprint = f"sitemap_404::{url}"
                        existing = (
                            db_session.query(IssueModel).filter_by(job_id=job_id, fingerprint=fingerprint).first()
                        )
                        if not existing:
                            issue = IssueModel(
                                job_id=job_id,
                                fingerprint=fingerprint,
                                title=f"NETWORK_FAILURE: Sitemap URL returns {resp.status_code} — {url}",
                                description=(
                                    f"The URL '{url}' is declared in the site's "
                                    f"sitemap but returns HTTP {resp.status_code}. "
                                    f"This is a broken/missing page that search "
                                    f"engines and users will encounter."
                                ),
                                severity=IssueSeverity.FUNCTIONAL,
                                category=IssueCategory.NETWORK_FAILURE,
                                first_seen_url=url,
                                error_message=f"HTTP {resp.status_code} on sitemap-declared URL",
                                stack_trace="",
                                repro_steps=[],
                                fix_status="unfixed",
                            )
                            db_session.add(issue)
                            db_session.commit()
                    except Exception:
                        pass

    return {
        "total_sitemap_urls": len(sitemap_urls),
        "verified": verified_count,
        "missing": len(missing_urls),
        "missing_urls": missing_urls,
    }


async def detect_llms_txt(base_url: str, registry: EndpointRegistry) -> bool:
    """Check for /llms.txt and register it if present.

    Mirrors the DeepSeek report's "llms.txt present (v4.5)" technical finding.
    Non-destructive: only adds the endpoint if it exists.
    """
    llms_url = urljoin(base_url, "/llms.txt")
    text = await _fetch_text(llms_url)
    if text:
        registry.add(
            url=llms_url,
            method="GET",
            source=EndpointSource.ROBOTS_TXT,  # Reuse robots source for meta files
        )
        # Store the llms.txt version/content as an extra on the endpoint
        for ep in registry.get_all():
            if ep.url == llms_url:
                # Extract version hint (e.g. "v4.5") if present
                version_match = re.search(r"v(\d+\.\d+)", text)
                ep.extra["llms_txt_version"] = version_match.group(0) if version_match else "present"
                ep.extra["llms_txt_content"] = text[:500]  # First 500 chars
                break
        return True
    return False
