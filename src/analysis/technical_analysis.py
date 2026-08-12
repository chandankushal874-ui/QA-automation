"""Technical site analysis layer — DeepSeek auditor parity.

Provides CMS detection, robots.txt content analysis, sitemap categorization,
and contact-form field analysis. Results are stored on ``job.config`` under
the ``technical_findings`` key and rendered by ``report_generator.py``.

All functions are *best-effort*: any exception is caught and logged so the
crawl pipeline never breaks because of a technical-analysis failure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class CMSFinding:
    """Result of CMS detection probing."""

    detected: str = "Unknown"
    version: str | None = None
    evidence: list[str] = field(default_factory=list)


@dataclass
class RobotsFinding:
    """Parsed ``robots.txt`` content analysis."""

    present: bool = False
    ai_crawlers_allowed: list[str] = field(default_factory=list)
    ai_crawlers_disallowed: list[str] = field(default_factory=list)
    disallowed_paths: list[str] = field(default_factory=list)
    sitemaps_declared: list[str] = field(default_factory=list)
    raw_snippet: str = ""


@dataclass
class SitemapCatalog:
    """Categorization of discovered sitemaps."""

    total: int = 0
    entries: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FormFinding:
    """Analysis of forms discovered during the crawl."""

    total_forms: int = 0
    with_csrf_token: int = 0
    with_honeypot: int = 0
    avg_fields: float = 0.0
    antipattern_forms: int = 0


# ---------------------------------------------------------------------------
# CMS detection
# ---------------------------------------------------------------------------

_WP_PROBE_PATHS = (
    "/wp-admin/",
    "/wp-login.php",
    "/wp-content/",
    "/xmlrpc.php",
)
_DRUPAL_PROBE_PATHS = (
    "/user/login",
    "/sites/default/files/",
    "/core/drupal.js",
)
_JOOMLA_PROBE_PATHS = (
    "/administrator/",
    "/components/com_content/",
)
_SHOPIFY_MARKER = re.compile(r"cdn\.shopify\.com|Shopify\.theme", re.IGNORECASE)
_SQUARESPACE_MARKER = re.compile(r"squarespace\.com|static1\.squarespace\.com", re.IGNORECASE)
_WIX_MARKER = re.compile(r"wix\.com|wixstatic\.com", re.IGNORECASE)


def detect_cms(base_url: str, registry: Any | None = None) -> CMSFinding:
    """Probe common CMS fingerprints.

    Uses the registry's visited endpoints first (free) and falls back to
    lightweight HTTP HEAD/GET probes for paths that were not crawled.
    """
    finding = CMSFinding()
    visited_paths: set[str] = set()
    if registry is not None:
        try:
            for ep in registry.get_all():
                try:
                    visited_paths.add(ep.path)
                except Exception:
                    continue
        except Exception:
            visited_paths = set()

    def _was_visited(path: str) -> bool:
        return any(p == path or p.startswith(path) for p in visited_paths)

    def _probe(path: str) -> int | None:
        """Return status code for ``path`` or ``None`` if unreachable."""
        if _was_visited(path):
            # Look up the recorded status from the registry
            if registry is not None:
                for ep in registry.get_all():
                    if ep.path == path and ep.last_status_code is not None:
                        return ep.last_status_code
            return None
        try:
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                r = client.head(f"{base_url.rstrip('/')}{path}", allow_redirects=True)
                if r.status_code == 405:  # HEAD not allowed — try GET
                    r = client.get(f"{base_url.rstrip('/')}{path}", follow_redirects=True)
                return r.status_code
        except Exception as e:
            logger.debug(f"CMS probe failed for {path}: {e}")
            return None

    # WordPress
    wp_hits = []
    for path in _WP_PROBE_PATHS:
        code = _probe(path)
        if code and code < 404:
            wp_hits.append(f"{path} → HTTP {code}")
    if wp_hits:
        finding.detected = "WordPress"
        finding.evidence = wp_hits
        # Try to read wp-generator meta from any crawled HTML
        if registry is not None:
            for ep in registry.get_all():
                html = ep.extra.get("html_snippet", "") if hasattr(ep, "extra") else ""
                if html:
                    m = re.search(r'<meta name="generator" content="WordPress ([\d.]+)"', html, re.IGNORECASE)
                    if m:
                        finding.version = m.group(1)
                        break
        return finding

    # Drupal
    drupal_hits = []
    for path in _DRUPAL_PROBE_PATHS:
        code = _probe(path)
        if code and code < 404:
            drupal_hits.append(f"{path} → HTTP {code}")
    if drupal_hits:
        finding.detected = "Drupal"
        finding.evidence = drupal_hits
        return finding

    # Joomla
    joomla_hits = []
    for path in _JOOMLA_PROBE_PATHS:
        code = _probe(path)
        if code and code < 404:
            joomla_hits.append(f"{path} → HTTP {code}")
    if joomla_hits:
        finding.detected = "Joomla"
        finding.evidence = joomla_hits
        return finding

    # SaaS markers — scan crawled HTML for fingerprints
    if registry is not None:
        all_html = ""
        for ep in registry.get_all()[:50]:  # sample first 50 pages
            html = ep.extra.get("html_snippet", "") if hasattr(ep, "extra") else ""
            if html:
                all_html += html[:5000]
        if _SHOPIFY_MARKER.search(all_html):
            finding.detected = "Shopify"
            finding.evidence = ["cdn.shopify.com / Shopify.theme markers in HTML"]
            return finding
        if _SQUARESPACE_MARKER.search(all_html):
            finding.detected = "Squarespace"
            finding.evidence = ["squarespace.com markers in HTML"]
            return finding
        if _WIX_MARKER.search(all_html):
            finding.detected = "Wix"
            finding.evidence = ["wix.com / wixstatic.com markers in HTML"]
            return finding

    # If all probes 404'd, conclude "not WordPress" (DeepSeek-style)
    if wp_hits == [] and all(_probe(p) == 404 for p in _WP_PROBE_PATHS if _probe(p) is not None):
        finding.detected = "Custom / Static (not WordPress, Drupal, or Joomla)"
        finding.evidence = ["All CMS probe paths returned 404"]

    return finding


# ---------------------------------------------------------------------------
# robots.txt analysis
# ---------------------------------------------------------------------------

_AI_CRAWLER_TOKENS = (
    "GPTBot",
    "ChatGPT-User",
    "Google-Extended",
    "PerplexityBot",
    "ClaudeBot",
    "Claude-Web",
    "anthropic-ai",
    "CCBot",
    "OAI-SearchBot",
    "Bytespider",
    "Applebot-Extended",
)


def analyze_robots_txt(base_url: str) -> RobotsFinding:
    """Fetch and parse ``robots.txt`` for AI-crawler allowances and disallows."""
    finding = RobotsFinding()
    robots_url = f"{base_url.rstrip('/')}/robots.txt"
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            r = client.get(robots_url)
            if r.status_code != 200 or not r.text.strip():
                return finding
            finding.present = True
            text = r.text
            finding.raw_snippet = text[:500]
    except Exception as e:
        logger.debug(f"robots.txt fetch failed: {e}")
        return finding

    # Parse User-agent / Disallow / Allow / Sitemap blocks
    current_agents: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#")[0].strip()
        if not line:
            continue
        if ":" not in line:
            continue
        directive, _, value = line.partition(":")
        directive = directive.strip().lower()
        value = value.strip()

        if directive == "user-agent":
            current_agents = [value]
        elif directive == "disallow":
            if value == "" or value == "/":
                # Empty disallow = allow everything for this agent
                pass
            else:
                finding.disallowed_paths.append(value)
                for agent in current_agents:
                    if agent in _AI_CRAWLER_TOKENS:
                        finding.ai_crawlers_disallowed.append(agent)
        elif directive == "allow":
            for agent in current_agents:
                if agent in _AI_CRAWLER_TOKENS and agent not in finding.ai_crawlers_allowed:
                    finding.ai_crawlers_allowed.append(agent)
        elif directive == "sitemap":
            finding.sitemaps_declared.append(value)

    # Dedupe
    finding.ai_crawlers_allowed = list(dict.fromkeys(finding.ai_crawlers_allowed))
    finding.ai_crawlers_disallowed = list(dict.fromkeys(finding.ai_crawlers_disallowed))
    finding.disallowed_paths = list(dict.fromkeys(finding.disallowed_paths))
    finding.sitemaps_declared = list(dict.fromkeys(finding.sitemaps_declared))
    return finding


# ---------------------------------------------------------------------------
# Sitemap categorization
# ---------------------------------------------------------------------------


def categorize_sitemaps(base_url: str, registry: Any | None = None) -> SitemapCatalog:
    """Fetch ``sitemap.xml`` (or sitemap index) and categorize sub-sitemaps."""
    catalog = SitemapCatalog()
    sitemap_url = f"{base_url.rstrip('/')}/sitemap.xml"
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            r = client.get(sitemap_url)
            if r.status_code != 200 or not r.text.strip():
                return catalog
            xml_text = r.text
    except Exception as e:
        logger.debug(f"sitemap.xml fetch failed: {e}")
        return catalog

    # Detect sitemap index (contains <sitemap> sub-elements)
    sub_sitemaps = re.findall(r"<loc>([^<]+)</loc>", xml_text)
    if "<sitemap" in xml_text.lower():
        # It's a sitemap index
        for loc in sub_sitemaps:
            name = urlparse(loc).path.rsplit("/", 1)[-1] or loc
            catalog.entries.append({"url": loc, "name": name, "type": "index-child"})
        catalog.total = len(catalog.entries)
    else:
        # Single sitemap — count URLs
        url_count = len(sub_sitemaps)
        catalog.entries.append({"url": sitemap_url, "name": "sitemap.xml", "type": "single", "url_count": url_count})
        catalog.total = 1

    return catalog


# ---------------------------------------------------------------------------
# Form analysis
# ---------------------------------------------------------------------------


_CSRF_PATTERNS = (
    re.compile(r'name=["\']csrf[_-]?token["\']', re.IGNORECASE),
    re.compile(r'name=["\']_csrf["\']', re.IGNORECASE),
    re.compile(r'name=["\']csrfmiddlewaretoken["\']', re.IGNORECASE),
    re.compile(r'name=["\']authenticity_token["\']', re.IGNORECASE),
)
_HONEYPOT_PATTERNS = (
    re.compile(r'name=["\'](website|url|phone|fax|comment)["\'].*style=["\']display:\s*none', re.IGNORECASE),
    re.compile(r'class=["\']honeypot["\']', re.IGNORECASE),
    re.compile(r'name=["\']hp[_-]?field["\']', re.IGNORECASE),
)


def analyze_forms(html_content: str) -> FormFinding:
    """Analyze ``<form>`` elements in an HTML string."""
    finding = FormFinding()
    if not html_content:
        return finding

    forms = re.findall(r"<form\b[^>]*>(.*?)</form>", html_content, re.IGNORECASE | re.DOTALL)
    finding.total_forms = len(forms)
    if not forms:
        return finding

    field_counts: list[int] = []
    for form_html in forms:
        inputs = re.findall(r"<(?:input|select|textarea)\b", form_html, re.IGNORECASE)
        field_counts.append(len(inputs))
        if any(p.search(form_html) for p in _CSRF_PATTERNS):
            finding.with_csrf_token += 1
        if any(p.search(form_html) for p in _HONEYPOT_PATTERNS):
            finding.with_honeypot += 1
        # GET-method form on sensitive path = antipattern (already flagged by mining)
        method_match = re.search(r'method=["\']?(\w+)', form_html, re.IGNORECASE)
        if method_match and method_match.group(1).upper() == "GET":
            finding.antipattern_forms += 1

    finding.avg_fields = sum(field_counts) / len(field_counts) if field_counts else 0.0
    return finding


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_technical_analysis(base_url: str, registry: Any | None = None) -> dict[str, Any]:
    """Run all technical-analysis passes and return a serializable dict.

    This is the single entry point called by ``engine.py``. Every sub-analysis
    is wrapped in try/except so one failure cannot abort the others.
    """
    findings: dict[str, Any] = {}

    try:
        findings["cms"] = detect_cms(base_url, registry).__dict__
    except Exception as e:
        logger.warning(f"Technical analysis (CMS) failed (non-fatal): {e}")
        findings["cms"] = CMSFinding().__dict__

    try:
        findings["robots"] = analyze_robots_txt(base_url).__dict__
    except Exception as e:
        logger.warning(f"Technical analysis (robots.txt) failed (non-fatal): {e}")
        findings["robots"] = RobotsFinding().__dict__

    try:
        findings["sitemaps"] = categorize_sitemaps(base_url, registry).__dict__
    except Exception as e:
        logger.warning(f"Technical analysis (sitemaps) failed (non-fatal): {e}")
        findings["sitemaps"] = SitemapCatalog().__dict__

    # Form analysis — aggregate across all crawled HTML snippets in registry
    try:
        aggregate = FormFinding()
        if registry is not None:
            for ep in registry.get_all():
                html = ep.extra.get("html_snippet", "") if hasattr(ep, "extra") else ""
                if html:
                    partial = analyze_forms(html)
                    aggregate.total_forms += partial.total_forms
                    aggregate.with_csrf_token += partial.with_csrf_token
                    aggregate.with_honeypot += partial.with_honeypot
                    aggregate.antipattern_forms += partial.antipattern_forms
        findings["forms"] = aggregate.__dict__
    except Exception as e:
        logger.warning(f"Technical analysis (forms) failed (non-fatal): {e}")
        findings["forms"] = FormFinding().__dict__

    return findings
