"""Console error capture and classification utility.

This module is the cross-cutting concern for console error checking.
Every browser interaction uses this to capture and classify ALL console output:
- console.error / console.warn / console.log
- Unhandled promise rejections
- CORS errors, CSP violations, mixed content warnings
- Network failures (failed fetch/XHR)
- Resource load failures (broken images, missing CSS/JS)
- Framework-specific errors (React error boundaries, Vue warnings, etc.)

Every page visit produces a ConsoleReport attached to the crawl result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from src.core.scrubber import scrub_text


class ConsoleSeverity(Enum):
    """Console message severity classification.

    CRITICAL > ERROR > WARNING > INFO
    """

    CRITICAL = "critical"  # App-breaking: uncaught exceptions, page crashes
    ERROR = "error"  # Functional impact: failed API calls, runtime errors
    WARNING = "warning"  # Potential issues: deprecations, CORS, CSP
    INFO = "info"  # Noise: debug logs, informational messages


class ConsoleCategory(Enum):
    """Categories for console messages to enable targeted analysis."""

    JS_ERROR = "js_error"  # JavaScript runtime errors
    UNHANDLED_REJECTION = "unhandled_rejection"  # Unhandled promise rejections
    CORS_ERROR = "cors_error"  # Cross-origin request failures
    CSP_VIOLATION = "csp_violation"  # Content Security Policy violations
    MIXED_CONTENT = "mixed_content"  # HTTP resources on HTTPS pages
    NETWORK_FAILURE = "network_failure"  # Failed fetch/XHR requests
    RESOURCE_LOAD_FAILURE = "resource_load_failure"  # Broken images, missing assets
    FRAMEWORK_ERROR = "framework_error"  # React/Vue/Angular specific errors
    DEPRECATION = "deprecation"  # Deprecation warnings
    SECURITY = "security"  # Security-related warnings
    GA_POST_FAILURE = "ga_post_failure"  # Google Analytics/Ads POST failures (net::ERR_ABORTED)
    # SPA client-side router 404 — the page returns HTTP 200 but the
    # router could not match the requested path (DeepSeek report Step 1).
    # Mirrors Angular's "Cannot match any routes", React Router's
    # "No routes matched location", and Vue Router's "No match" errors.
    SPA_ROUTE_ERROR = "spa_route_error"
    GENERAL_WARNING = "general_warning"  # General console.warn
    GENERAL_ERROR = "general_error"  # General console.error
    GENERAL_LOG = "general_log"  # console.log / console.info


# Patterns for classifying console messages
_CORS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"access-control-allow-origin", re.IGNORECASE),
    re.compile(r"blocked by CORS", re.IGNORECASE),
    re.compile(r"cross-origin", re.IGNORECASE),
    re.compile(r"No 'Access-Control-Allow-Origin'", re.IGNORECASE),
]

_CSP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"content.?security.?policy", re.IGNORECASE),
    re.compile(r"refused to .+ because .+ violates", re.IGNORECASE),
    re.compile(r"CSP", re.IGNORECASE),
]

_MIXED_CONTENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"mixed content", re.IGNORECASE),
    re.compile(r"was loaded over HTTPS, but requested an insecure", re.IGNORECASE),
]

_FRAMEWORK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"react", re.IGNORECASE),
    re.compile(r"vue warn", re.IGNORECASE),
    re.compile(r"angular", re.IGNORECASE),
    re.compile(r"error boundary", re.IGNORECASE),
    re.compile(r"next\.?js", re.IGNORECASE),
    re.compile(r"unhandled runtime error", re.IGNORECASE),
]

_DEPRECATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"deprecat", re.IGNORECASE),
    re.compile(r"will be removed", re.IGNORECASE),
]

_NETWORK_FAILURE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"net::ERR_", re.IGNORECASE),
    re.compile(r"failed to fetch", re.IGNORECASE),
    re.compile(r"NetworkError", re.IGNORECASE),
    re.compile(r"ERR_CONNECTION_REFUSED", re.IGNORECASE),
    re.compile(r"ERR_NAME_NOT_RESOLVED", re.IGNORECASE),
    re.compile(r"websocket", re.IGNORECASE),
    re.compile(r"ws://|wss://", re.IGNORECASE),
]

_RESOURCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"failed to load resource", re.IGNORECASE),
    re.compile(r"404 \(Not Found\)", re.IGNORECASE),
    re.compile(r"GET .+ (4\d{2}|5\d{2})", re.IGNORECASE),
]

# Google Analytics / Google Ads POST failure patterns.
# These match net::ERR_ABORTED and similar failures on GA4/GTM/Ads
# collection endpoints — mirrors the DeepSeek report's "Google Analytics /
# Tag Manager POST Failures" finding.
_GA_POST_FAILURE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"google\.com/ccm/collect", re.IGNORECASE),
    re.compile(r"google\.com/rmkt/collect", re.IGNORECASE),
    re.compile(r"analytics\.google\.com/g/collect", re.IGNORECASE),
    re.compile(r"googletagmanager\.com.*ERR_ABORTED", re.IGNORECASE),
    re.compile(r"doubleclick\.net.*ERR_ABORTED", re.IGNORECASE),
]


# SPA client-side router "no route matched" patterns (DeepSeek report Step 1).
# These fire when the SPA returns HTTP 200 (the shell HTML loaded fine) but
# the client router could not match the requested path — an effective 404
# that the HTTP status code hides. Each pattern is paired with the framework
# it originates from so the report can attribute the error correctly.
#
# Coverage:
#   - Angular Router:  "Cannot match any routes. URL Segment: '...'"
#   - React Router:    "No routes matched location", "No route matches"
#   - Vue Router:      "No match found for location with path", "[Vue warn]: No match"
#   - SvelteKit:       "No route for", "Not found"
#   - Next.js:         "The page could not be found", "404 This page could not be found"
#   - Generic:         "route not found", "no route matched"
_SPA_ROUTE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Cannot match any routes", re.IGNORECASE), "angular"),
    (re.compile(r"No routes matched location", re.IGNORECASE), "react-router"),
    (re.compile(r"No route matched", re.IGNORECASE), "react-router"),
    (re.compile(r"No match found for location with path", re.IGNORECASE), "vue-router"),
    (re.compile(r"\[Vue warn\]:\s*No match", re.IGNORECASE), "vue-router"),
    (re.compile(r"No route for\b", re.IGNORECASE), "sveltekit"),
    (re.compile(r"404.*This page could not be found", re.IGNORECASE), "nextjs"),
    (re.compile(r"The page could not be found", re.IGNORECASE), "nextjs"),
    (re.compile(r"route not found", re.IGNORECASE), "generic"),
    (re.compile(r"no route matched", re.IGNORECASE), "generic"),
]


def detect_spa_route_error(message: str) -> str | None:
    """Return the SPA framework name if ``message`` is a client-router 404.

    Returns ``None`` for non-SPA messages. Used by the crawl engine to mark
    an endpoint's *effective* status as a 404 even when the HTTP response
    was 200 (the SPA shell loaded but the route did not resolve).
    """
    for pattern, framework in _SPA_ROUTE_PATTERNS:
        if pattern.search(message):
            return framework
    return None


# CSP directive + origin extraction patterns.
# Used to group multiple CSP violations that share the same
# (directive, origin_domain) into a single deduplicated issue,
# mirroring the DeepSeek report's per-directive grouping.
_CSP_DIRECTIVE_PATTERN: re.Pattern[str] = re.compile(
    r"violates the following Content Security Policy directive: \"([^\"]+)\"",
    re.IGNORECASE,
)
_CSP_ORIGIN_PATTERN: re.Pattern[str] = re.compile(r"https?://([a-zA-Z0-9.\-]+)")


def extract_csp_metadata(message: str) -> dict[str, str]:
    """Extract the CSP directive and origin domain from a CSP violation message.

    Returns a dict with keys ``directive`` and ``origin_domain`` so that
    multiple violations of the same directive for the same external domain
    collapse into one fingerprint (e.g. all ``ad.doubleclick.net``
    ``connect-src`` violations become one issue).
    """
    directive_match = _CSP_DIRECTIVE_PATTERN.search(message)
    origin_match = _CSP_ORIGIN_PATTERN.search(message)
    directive = "unknown"
    if directive_match:
        # Take the first token of the directive (e.g. "script-src" from
        # "script-src 'self' 'unsafe-inline' ...")
        directive = directive_match.group(1).split()[0].lower()
    origin_domain = origin_match.group(1).lower() if origin_match else "unknown"
    return {"directive": directive, "origin_domain": origin_domain}


@dataclass
class ConsoleEntry:
    """A single captured console message."""

    timestamp: datetime
    severity: ConsoleSeverity
    category: ConsoleCategory
    message: str
    source_url: str  # URL of the page that emitted the message
    source_location: str = ""  # file:line:col if available
    stack_trace: str = ""  # Full stack trace if available
    browser: str = ""  # Which browser engine captured this
    raw_type: str = ""  # Original Playwright message type (e.g., 'error', 'warning')
    extra: dict[str, Any] = field(default_factory=dict)

    def is_actionable(self) -> bool:
        """Return True if this entry warrants investigation (not just noise)."""
        return self.severity in (ConsoleSeverity.CRITICAL, ConsoleSeverity.ERROR)

    def fingerprint(self) -> str:
        """Generate a dedup fingerprint for this entry.

        Used to collapse duplicate errors across multiple page visits.
        CSP violations are grouped by (directive, origin_domain) so that
        repeated violations of the same directive for the same external
        service collapse into a single issue (mirrors the DeepSeek
        report's per-directive grouping).
        """
        # CSP violations: group by directive + origin domain instead of
        # raw message text. This collapses e.g. 6 individual
        # ad.doubleclick.net connect-src advisories into 1 grouped issue.
        if self.category == ConsoleCategory.CSP_VIOLATION:
            meta = extract_csp_metadata(self.message)
            return f"csp::{meta['directive']}::{meta['origin_domain']}"

        # Normalize the message by stripping line numbers and dynamic values
        normalized = re.sub(r"\d+", "N", self.message[:200])
        normalized = re.sub(r"https?://[^\s]+", "URL", normalized)
        return f"{self.category.value}::{normalized}"


@dataclass
class ConsoleReport:
    """Aggregated console report for a single page visit."""

    url: str
    browser: str
    entries: list[ConsoleEntry] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    @property
    def critical_count(self) -> int:
        return sum(1 for e in self.entries if e.severity == ConsoleSeverity.CRITICAL)

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.entries if e.severity == ConsoleSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for e in self.entries if e.severity == ConsoleSeverity.WARNING)

    @property
    def has_blocking_errors(self) -> bool:
        """Return True if any critical errors were found (page may be broken)."""
        return self.critical_count > 0

    @property
    def has_errors(self) -> bool:
        """Return True if any errors (critical or regular) were found."""
        return self.critical_count > 0 or self.error_count > 0

    def get_actionable_entries(self) -> list[ConsoleEntry]:
        """Return only entries that warrant investigation."""
        return [e for e in self.entries if e.is_actionable()]

    def get_entries_by_category(self, category: ConsoleCategory) -> list[ConsoleEntry]:
        """Return entries filtered by category."""
        return [e for e in self.entries if e.category == category]

    def summary(self) -> dict[str, int]:
        """Return a summary of entry counts by severity."""
        return {
            "critical": self.critical_count,
            "error": self.error_count,
            "warning": self.warning_count,
            "info": sum(1 for e in self.entries if e.severity == ConsoleSeverity.INFO),
            "total": len(self.entries),
        }

    def finalize(self) -> None:
        """Mark the report as complete."""
        self.finished_at = datetime.now(UTC)


def classify_message(message_text: str, message_type: str) -> tuple[ConsoleSeverity, ConsoleCategory]:
    """Classify a raw console message into severity and category.

    Args:
        message_text: The text content of the console message.
        message_type: The Playwright message type ('error', 'warning', 'log', 'info', etc.)

    Returns:
        Tuple of (severity, category).
    """
    text = message_text.strip()

    # Check for specific patterns first (most specific → least specific)

    # CORS errors
    if any(p.search(text) for p in _CORS_PATTERNS):
        return ConsoleSeverity.WARNING, ConsoleCategory.CORS_ERROR

    # CSP violations
    if any(p.search(text) for p in _CSP_PATTERNS):
        return ConsoleSeverity.WARNING, ConsoleCategory.CSP_VIOLATION

    # Mixed content
    if any(p.search(text) for p in _MIXED_CONTENT_PATTERNS):
        return ConsoleSeverity.WARNING, ConsoleCategory.MIXED_CONTENT

    # Google Analytics / Ads POST failures (check before generic network)
    if any(p.search(text) for p in _GA_POST_FAILURE_PATTERNS):
        return ConsoleSeverity.WARNING, ConsoleCategory.GA_POST_FAILURE

    # SPA client-side router 404 (check before framework patterns — more specific)
    # DeepSeek report Step 1: HTTP 200 shell loaded but the router could not
    # match the path. This is an effective 404 even though the HTTP status is 200.
    if any(p.search(text) for p, _ in _SPA_ROUTE_PATTERNS):
        return ConsoleSeverity.ERROR, ConsoleCategory.SPA_ROUTE_ERROR

    # Network failures
    if any(p.search(text) for p in _NETWORK_FAILURE_PATTERNS):
        return ConsoleSeverity.ERROR, ConsoleCategory.NETWORK_FAILURE

    # Resource load failures
    if any(p.search(text) for p in _RESOURCE_PATTERNS):
        return ConsoleSeverity.ERROR, ConsoleCategory.RESOURCE_LOAD_FAILURE

    # Framework-specific errors
    if any(p.search(text) for p in _FRAMEWORK_PATTERNS):
        severity = ConsoleSeverity.ERROR if message_type == "error" else ConsoleSeverity.WARNING
        return severity, ConsoleCategory.FRAMEWORK_ERROR

    # Deprecation warnings
    if any(p.search(text) for p in _DEPRECATION_PATTERNS):
        return ConsoleSeverity.WARNING, ConsoleCategory.DEPRECATION

    # Unhandled promise rejections
    if "unhandled" in text.lower() and ("rejection" in text.lower() or "promise" in text.lower()):
        return ConsoleSeverity.CRITICAL, ConsoleCategory.UNHANDLED_REJECTION

    # Uncaught exceptions
    if text.lower().startswith("uncaught") or "uncaught exception" in text.lower():
        return ConsoleSeverity.CRITICAL, ConsoleCategory.JS_ERROR

    # Fall back to type-based classification
    if message_type == "error":
        return ConsoleSeverity.ERROR, ConsoleCategory.GENERAL_ERROR

    if message_type == "warning":
        return ConsoleSeverity.WARNING, ConsoleCategory.GENERAL_WARNING

    return ConsoleSeverity.INFO, ConsoleCategory.GENERAL_LOG


class ConsoleCapture:
    """Captures and classifies all console output from a browser page.

    Usage:
        capture = ConsoleCapture(url="https://example.com", browser="chromium")
        # Attach to a Playwright page:
        # page.on("console", capture.on_console_message)
        # page.on("pageerror", capture.on_page_error)
        # ... interact with page ...
        report = capture.finalize()
    """

    def __init__(self, url: str, browser: str = "unknown") -> None:
        self._report = ConsoleReport(url=url, browser=browser)

    @property
    def report(self) -> ConsoleReport:
        """Access the current report (even before finalization)."""
        return self._report

    def add_entry(
        self,
        message: str,
        message_type: str = "log",
        source_location: str = "",
        stack_trace: str = "",
        extra: dict[str, Any] | None = None,
    ) -> ConsoleEntry:
        """Add a console entry, auto-classifying severity and category.

        This is the primary method for adding captured console messages.
        """
        severity, category = classify_message(message, message_type)
        clean_message = scrub_text(message)
        clean_stack = scrub_text(stack_trace)

        entry = ConsoleEntry(
            timestamp=datetime.now(UTC),
            severity=severity,
            category=category,
            message=clean_message,
            source_url=self._report.url,
            source_location=source_location,
            stack_trace=clean_stack,
            browser=self._report.browser,
            raw_type=message_type,
            extra=extra or {},
        )
        self._report.entries.append(entry)
        return entry

    def add_page_error(self, error_message: str, stack_trace: str = "") -> ConsoleEntry:
        """Record a page-level error (uncaught exception / unhandled rejection).

        These are always CRITICAL severity.
        """
        clean_message = scrub_text(error_message)
        clean_stack = scrub_text(stack_trace)

        entry = ConsoleEntry(
            timestamp=datetime.now(UTC),
            severity=ConsoleSeverity.CRITICAL,
            category=ConsoleCategory.JS_ERROR,
            message=clean_message,
            source_url=self._report.url,
            stack_trace=clean_stack,
            browser=self._report.browser,
            raw_type="pageerror",
        )
        self._report.entries.append(entry)
        return entry

    def add_network_failure(
        self,
        url: str,
        method: str = "GET",
        status_code: int = 0,
        status_text: str = "",
        error_text: str = "",
    ) -> ConsoleEntry:
        """Record a failed network request."""
        clean_url = scrub_text(url)
        clean_err = scrub_text(error_text)
        message = f"{method} {clean_url} — {status_code} {status_text}".strip()
        if clean_err:
            message += f" ({clean_err})"

        severity = ConsoleSeverity.CRITICAL if status_code >= 500 else ConsoleSeverity.ERROR

        entry = ConsoleEntry(
            timestamp=datetime.now(UTC),
            severity=severity,
            category=ConsoleCategory.NETWORK_FAILURE,
            message=message,
            source_url=self._report.url,
            browser=self._report.browser,
            raw_type="network",
            extra={
                "request_url": clean_url,
                "method": method,
                "status_code": status_code,
                "status_text": status_text,
                "error_text": clean_err,
            },
        )
        self._report.entries.append(entry)
        return entry

    def add_resource_failure(self, resource_url: str, resource_type: str = "unknown") -> ConsoleEntry:
        """Record a failed resource load (broken image, missing CSS/JS, etc.)."""
        clean_url = scrub_text(resource_url)
        entry = ConsoleEntry(
            timestamp=datetime.now(UTC),
            severity=ConsoleSeverity.ERROR,
            category=ConsoleCategory.RESOURCE_LOAD_FAILURE,
            message=f"Failed to load {resource_type}: {clean_url}",
            source_url=self._report.url,
            browser=self._report.browser,
            raw_type="resource",
            extra={"resource_url": clean_url, "resource_type": resource_type},
        )
        self._report.entries.append(entry)
        return entry

    def finalize(self) -> ConsoleReport:
        """Finalize and return the complete console report."""
        self._report.finalize()
        return self._report
