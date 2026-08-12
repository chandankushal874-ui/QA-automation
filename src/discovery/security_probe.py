"""Security / common-endpoint probe.

After the main crawl completes, this module probes a curated list of
~120 common security-relevant paths that may exist on the target but are
not linked from any crawled page. Examples:

- Sensitive files: ``.env``, ``.git/config``, ``.git/HEAD``
- Admin panels: ``/admin``, ``/wp-admin``, ``/wp-login.php``
- Debug/diagnostic endpoints: ``/debug``, ``/actuator``, ``/metrics``
- Common files: ``robots.txt``, ``sitemap.xml``, ``security.txt``
- API health/spec: ``/api/health``, ``/swagger.json``, ``/openapi.json``

**SPA false-positive elimination** (see ``crawl_testing_full_analysis.md``):

Single Page Applications (Angular, React, Vue, Next.js, SvelteKit) use
catch-all routing — the server returns ``index.html`` with HTTP 200 for
*every* URL, including non-existent paths. Without validation, the probe
would flag every probed path as "exposed", producing massive false
positives. To eliminate this, the probe pipeline applies five checks
before flagging a path as a real finding:

1. **Content-Type check** — if a non-HTML path returns ``text/html``,
   it's the SPA shell, not the real file → false positive.
2. **Shell comparison (soft-404 fingerprint)** — the body of the
   catch-all shell captured during SPA detection is compared against
   every 2xx response. HTML-OK paths (``/admin``, ``/login``, …) pass
   the Content-Type gate, so this is what stops them from being flagged
   "exposed (HTTP 200)" when the server only returned the shell —
   an effective "404 SPA Error".
3. **Body signature validation** — verifies the response body matches
   the expected format for the file type (e.g. ``.env`` must contain
   ``KEY=VALUE`` pairs, ``.git/config`` must start with ``[core]``).
4. **Size fingerprint** — SPA shells are typically 2–20 KB; real files
   fall outside this range for most sensitive file types.
5. **SPA pre-detection** — before probing, requests a clearly
   non-existent path to detect if the target is a SPA and enable body
   validation mode automatically.

Each path is probed with a lightweight HTTP HEAD (falling back to GET
when the server doesn't support HEAD — GET is always used when SPA
validation is needed to inspect the body). Responses are classified:

- **200 + validated** → exposed (potential finding). Severity depends on
  the path: sensitive files → ``BLOCKING``, admin/debug →
  ``FUNCTIONAL``, benign common files (robots.txt, sitemap.xml) →
  ``ADVISORY`` (info only).
- **200 + SPA fallback** → false positive (not recorded).
- **403 / 401** → protected (good posture, not recorded).
- **404 / 405** → absent (not recorded — the common case).
- **5xx** → server error (recorded as ``FUNCTIONAL``).

Findings are persisted as ``IssueCategory.SECURITY`` issues so they
appear in the report under a dedicated "Security/Common" category,
separate from console/JS/network issues found during the crawl.

This is a non-fatal best-effort pass — any per-path error is logged and
skipped so it never breaks the main crawl/report pipeline.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx

from src.models.database import Issue, IssueCategory, IssueSeverity

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.models.database import Job

logger = logging.getLogger(__name__)


# Curated list of ~120 common security-relevant paths to probe.
# Each entry is (path, severity_if_exposed, description).
# Severity guidance:
#   BLOCKING   — sensitive files that should never be public (.env, .git)
#   FUNCTIONAL — admin/debug panels that are exposed (attack surface)
#   ADVISORY   — benign common files (robots.txt, sitemap.xml) — info only
SECURITY_PROBE_PATHS: list[tuple[str, IssueSeverity, str]] = [
    # --- Sensitive files (BLOCKING if exposed) ---
    ("/.env", IssueSeverity.BLOCKING, "Environment file exposed — may contain secrets, API keys, DB credentials"),
    ("/.env.local", IssueSeverity.BLOCKING, "Local environment file exposed — may contain secrets"),
    ("/.env.production", IssueSeverity.BLOCKING, "Production environment file exposed — may contain secrets"),
    ("/.env.development", IssueSeverity.BLOCKING, "Development environment file exposed — may contain secrets"),
    ("/.env.staging", IssueSeverity.BLOCKING, "Staging environment file exposed — may contain secrets"),
    ("/.env.example", IssueSeverity.ADVISORY, "Example env file exposed — may reveal expected env vars (info)"),
    ("/.git/config", IssueSeverity.BLOCKING, "Git config exposed — repository metadata leak"),
    ("/.git/HEAD", IssueSeverity.BLOCKING, "Git HEAD ref exposed — full repository may be downloadable"),
    ("/.git/index", IssueSeverity.BLOCKING, "Git index exposed — repository staging area leak"),
    ("/.gitignore", IssueSeverity.ADVISORY, ".gitignore exposed — may reveal sensitive paths (info)"),
    ("/.DS_Store", IssueSeverity.BLOCKING, "macOS .DS_Store exposed — leaks directory structure and filenames"),
    ("/.htaccess", IssueSeverity.BLOCKING, "Apache .htaccess exposed — may reveal access-control / rewrite rules"),
    ("/.htpasswd", IssueSeverity.BLOCKING, "Apache .htpasswd exposed — may leak hashed credentials"),
    ("/web.config", IssueSeverity.BLOCKING, "IIS web.config exposed — may leak connection strings and handlers"),
    (
        "/crossdomain.xml",
        IssueSeverity.ADVISORY,
        "crossdomain.xml exposed — Flash/Silverlight cross-domain policy (info)",
    ),
    ("/clientaccesspolicy.xml", IssueSeverity.ADVISORY, "Silverlight client access policy exposed (info)"),
    ("/composer.json", IssueSeverity.BLOCKING, "Composer manifest exposed — leaks PHP dependencies and scripts"),
    ("/composer.lock", IssueSeverity.BLOCKING, "Composer lock file exposed — leaks exact dependency versions"),
    ("/package.json", IssueSeverity.ADVISORY, "package.json exposed — leaks JS dependencies and scripts (info)"),
    ("/package-lock.json", IssueSeverity.ADVISORY, "npm lock file exposed — leaks exact dependency versions (info)"),
    ("/yarn.lock", IssueSeverity.ADVISORY, "Yarn lock file exposed — leaks exact dependency versions (info)"),
    ("/Gemfile", IssueSeverity.ADVISORY, "Ruby Gemfile exposed — leaks Ruby dependencies (info)"),
    ("/requirements.txt", IssueSeverity.ADVISORY, "Python requirements exposed — leaks Python dependencies (info)"),
    ("/Pipfile", IssueSeverity.ADVISORY, "Pipfile exposed — leaks Python dependencies (info)"),
    ("/Pipfile.lock", IssueSeverity.ADVISORY, "Pipfile.lock exposed — leaks exact dependency versions (info)"),
    ("/docker-compose.yml", IssueSeverity.BLOCKING, "docker-compose file exposed — leaks service topology and secrets"),
    (
        "/docker-compose.yaml",
        IssueSeverity.BLOCKING,
        "docker-compose file exposed — leaks service topology and secrets",
    ),
    ("/Dockerfile", IssueSeverity.ADVISORY, "Dockerfile exposed — leaks build steps and base image (info)"),
    ("/.dockerignore", IssueSeverity.ADVISORY, ".dockerignore exposed — may reveal sensitive paths (info)"),
    ("/id_rsa", IssueSeverity.BLOCKING, "SSH private key exposed — full server compromise if valid"),
    ("/id_rsa.pub", IssueSeverity.ADVISORY, "SSH public key exposed — info only"),
    ("/.ssh/id_rsa", IssueSeverity.BLOCKING, "SSH private key exposed — full server compromise if valid"),
    ("/.ssh/known_hosts", IssueSeverity.ADVISORY, "SSH known_hosts exposed — leaks trusted hosts (info)"),
    ("/backup", IssueSeverity.BLOCKING, "Backup directory exposed — may contain DB dumps or config snapshots"),
    ("/backup.zip", IssueSeverity.BLOCKING, "Backup archive exposed — may contain full site + DB"),
    ("/backup.tar.gz", IssueSeverity.BLOCKING, "Backup archive exposed — may contain full site + DB"),
    ("/backup.sql", IssueSeverity.BLOCKING, "SQL database dump exposed — full data leak"),
    ("/db.sql", IssueSeverity.BLOCKING, "SQL database dump exposed — full data leak"),
    ("/dump.sql", IssueSeverity.BLOCKING, "SQL database dump exposed — full data leak"),
    ("/config", IssueSeverity.BLOCKING, "Config directory/file exposed — may contain secrets"),
    ("/config.json", IssueSeverity.BLOCKING, "Config JSON exposed — may contain secrets"),
    ("/config.yml", IssueSeverity.BLOCKING, "Config YAML exposed — may contain secrets"),
    ("/config.yaml", IssueSeverity.BLOCKING, "Config YAML exposed — may contain secrets"),
    ("/configuration", IssueSeverity.BLOCKING, "Configuration endpoint exposed — may contain secrets"),
    ("/settings.json", IssueSeverity.BLOCKING, "Settings JSON exposed — may contain secrets"),
    ("/credentials", IssueSeverity.BLOCKING, "Credentials file exposed — full credential leak"),
    ("/secret", IssueSeverity.BLOCKING, "Secret file exposed — full secret leak"),
    ("/.aws/credentials", IssueSeverity.BLOCKING, "AWS credentials file exposed — full cloud account compromise"),
    ("/.aws/config", IssueSeverity.BLOCKING, "AWS config exposed — leaks account IDs and region settings"),
    # --- Admin panels (FUNCTIONAL if exposed) ---
    ("/admin", IssueSeverity.FUNCTIONAL, "Admin panel reachable — verify authentication is enforced"),
    ("/admin/", IssueSeverity.FUNCTIONAL, "Admin panel reachable — verify authentication is enforced"),
    ("/administrator", IssueSeverity.FUNCTIONAL, "Administrator panel reachable"),
    ("/internal/admin", IssueSeverity.FUNCTIONAL, "Internal admin panel reachable"),
    ("/wp-admin", IssueSeverity.FUNCTIONAL, "WordPress admin panel reachable"),
    ("/wp-admin/", IssueSeverity.FUNCTIONAL, "WordPress admin panel reachable"),
    ("/wp-login.php", IssueSeverity.FUNCTIONAL, "WordPress login page reachable — brute-force attack surface"),
    ("/wp-config.php", IssueSeverity.BLOCKING, "WordPress config exposed — leaks DB credentials"),
    ("/manager", IssueSeverity.FUNCTIONAL, "Manager panel reachable (Tomcat / Spring Boot)"),
    ("/manager/html", IssueSeverity.FUNCTIONAL, "Tomcat manager HTML reachable — deploy/undeploy apps"),
    ("/console", IssueSeverity.FUNCTIONAL, "Console endpoint reachable — may be an admin shell"),
    ("/_admin", IssueSeverity.FUNCTIONAL, "Hidden admin panel reachable"),
    ("/admin.php", IssueSeverity.FUNCTIONAL, "Admin PHP page reachable"),
    ("/cpanel", IssueSeverity.FUNCTIONAL, "cPanel reachable — hosting control panel"),
    ("/plesk", IssueSeverity.FUNCTIONAL, "Plesk reachable — hosting control panel"),
    # --- Debug/diagnostic endpoints (FUNCTIONAL if exposed) ---
    ("/debug", IssueSeverity.FUNCTIONAL, "Debug endpoint exposed — may leak internal state"),
    ("/debug/", IssueSeverity.FUNCTIONAL, "Debug endpoint exposed — may leak internal state"),
    ("/_debug", IssueSeverity.FUNCTIONAL, "Hidden debug endpoint exposed"),
    ("/actuator", IssueSeverity.FUNCTIONAL, "Spring Boot actuator exposed — may expose env/heap/mappings"),
    ("/actuator/health", IssueSeverity.ADVISORY, "Spring Boot health endpoint — info only if exposed"),
    ("/actuator/env", IssueSeverity.BLOCKING, "Spring Boot env actuator exposed — leaks all environment properties"),
    ("/actuator/heapdump", IssueSeverity.BLOCKING, "Spring Boot heapdump exposed — leaks in-memory secrets"),
    ("/actuator/mappings", IssueSeverity.FUNCTIONAL, "Spring Boot mappings exposed — leaks all routes"),
    ("/actuator/beans", IssueSeverity.FUNCTIONAL, "Spring Boot beans exposed — leaks application wiring"),
    ("/actuator/configprops", IssueSeverity.BLOCKING, "Spring Boot configprops exposed — leaks configuration"),
    ("/metrics", IssueSeverity.FUNCTIONAL, "Metrics endpoint exposed — may leak internal counters"),
    ("/prometheus", IssueSeverity.FUNCTIONAL, "Prometheus metrics endpoint exposed — leaks internal counters"),
    ("/phpinfo.php", IssueSeverity.FUNCTIONAL, "phpinfo() page exposed — leaks full PHP/server config"),
    ("/info.php", IssueSeverity.FUNCTIONAL, "PHP info page exposed — leaks server config"),
    ("/server-info", IssueSeverity.FUNCTIONAL, "Server info endpoint exposed — may leak version/config"),
    ("/server-status", IssueSeverity.FUNCTIONAL, "Apache server-status exposed — leaks request handlers"),
    ("/status", IssueSeverity.ADVISORY, "Status endpoint — info only if exposed"),
    ("/healthz", IssueSeverity.ADVISORY, "Healthz endpoint — info only if exposed"),
    ("/health", IssueSeverity.ADVISORY, "Health endpoint — info only if exposed"),
    ("/health/check", IssueSeverity.ADVISORY, "Health check endpoint — info only if exposed"),
    ("/__debug__", IssueSeverity.FUNCTIONAL, "Django debug toolbar endpoint exposed"),
    ("/__nextjs_original-stack-frame", IssueSeverity.FUNCTIONAL, "Next.js debug stack-frame endpoint exposed"),
    ("/_next/data", IssueSeverity.ADVISORY, "Next.js data route — info only if exposed"),
    # --- API health/spec (ADVISORY — useful for inventory) ---
    ("/api/health", IssueSeverity.ADVISORY, "API health endpoint — info only"),
    ("/api/v1", IssueSeverity.ADVISORY, "API v1 root — info only"),
    ("/api/v2", IssueSeverity.ADVISORY, "API v2 root — info only"),
    ("/api/v3", IssueSeverity.ADVISORY, "API v3 root — info only"),
    ("/api-docs", IssueSeverity.ADVISORY, "API docs page — info only"),
    ("/swagger.json", IssueSeverity.ADVISORY, "Swagger/OpenAPI spec exposed — useful for API inventory"),
    ("/swagger-ui", IssueSeverity.ADVISORY, "Swagger UI reachable — interactive API explorer"),
    ("/swagger-ui.html", IssueSeverity.ADVISORY, "Swagger UI reachable — interactive API explorer"),
    ("/openapi.json", IssueSeverity.ADVISORY, "OpenAPI spec exposed — useful for API inventory"),
    ("/openapi.yaml", IssueSeverity.ADVISORY, "OpenAPI YAML spec exposed — useful for API inventory"),
    ("/redoc", IssueSeverity.ADVISORY, "ReDoc API docs reachable — info only"),
    ("/graphql", IssueSeverity.ADVISORY, "GraphQL endpoint reachable — may allow introspection"),
    ("/graphiql", IssueSeverity.FUNCTIONAL, "GraphiQL interactive IDE exposed — may allow arbitrary queries"),
    ("/playground", IssueSeverity.FUNCTIONAL, "GraphQL Playground exposed — may allow arbitrary queries"),
    ("/v1", IssueSeverity.ADVISORY, "API v1 root — info only"),
    ("/v2", IssueSeverity.ADVISORY, "API v2 root — info only"),
    # --- Common files (ADVISORY — expected, info only) ---
    ("/robots.txt", IssueSeverity.ADVISORY, "robots.txt present — review for disallowed paths"),
    ("/sitemap.xml", IssueSeverity.ADVISORY, "sitemap.xml present — useful for crawl coverage"),
    ("/sitemap_index.xml", IssueSeverity.ADVISORY, "sitemap index present — useful for crawl coverage"),
    ("/.well-known/security.txt", IssueSeverity.ADVISORY, "security.txt present — good security posture signal"),
    ("/humans.txt", IssueSeverity.ADVISORY, "humans.txt present — info only"),
    ("/ads.txt", IssueSeverity.ADVISORY, "ads.txt present — Google Ads verification (info)"),
    ("/security.txt", IssueSeverity.ADVISORY, "security.txt present — good security posture signal"),
    ("/favicon.ico", IssueSeverity.ADVISORY, "favicon.ico present — info only"),
    ("/llms.txt", IssueSeverity.ADVISORY, "llms.txt present — AI crawler instructions (info)"),
    ("/CHANGELOG.md", IssueSeverity.ADVISORY, "Changelog exposed — leaks version history (info)"),
    ("/README.md", IssueSeverity.ADVISORY, "README exposed — may leak project structure (info)"),
    ("/README.txt", IssueSeverity.ADVISORY, "README exposed — may leak project structure (info)"),
    ("/LICENSE", IssueSeverity.ADVISORY, "LICENSE file exposed — info only"),
    ("/VERSION", IssueSeverity.ADVISORY, "VERSION file exposed — leaks exact build version (info)"),
    ("/.svn/entries", IssueSeverity.BLOCKING, "Subversion metadata exposed — repository may be downloadable"),
    ("/.svn/wc.db", IssueSeverity.BLOCKING, "Subversion working-copy DB exposed — repository metadata leak"),
    ("/.hg/store", IssueSeverity.BLOCKING, "Mercurial store exposed — repository may be downloadable"),
    ("/CVS/Root", IssueSeverity.BLOCKING, "CVS Root exposed — repository metadata leak"),
    # --- Auth endpoints (ADVISORY — expected, info only) ---
    ("/login", IssueSeverity.ADVISORY, "Login page present — expected auth endpoint (info)"),
    ("/signin", IssueSeverity.ADVISORY, "Sign-in page present — expected auth endpoint (info)"),
    ("/signup", IssueSeverity.ADVISORY, "Sign-up page present — expected auth endpoint (info)"),
    ("/register", IssueSeverity.ADVISORY, "Registration page present — expected auth endpoint (info)"),
    ("/logout", IssueSeverity.ADVISORY, "Logout endpoint present — expected auth endpoint (info)"),
    ("/auth", IssueSeverity.ADVISORY, "Auth root present — expected auth endpoint (info)"),
    ("/oauth", IssueSeverity.ADVISORY, "OAuth endpoint present — expected auth endpoint (info)"),
    ("/token", IssueSeverity.ADVISORY, "Token endpoint present — expected auth endpoint (info)"),
    ("/callback", IssueSeverity.ADVISORY, "Callback endpoint present — expected OAuth callback (info)"),
]


class ProbeVerdict(StrEnum):
    """Verdict for a probed path after SPA false-positive elimination."""

    CONFIRMED_EXPOSED = "confirmed_exposed"  # Real finding — passed all checks
    FALSE_POSITIVE = "false_positive"  # SPA shell returned, not the real file
    SPA_FALLBACK = "spa_fallback"  # Route probe returned the SPA shell (client-side route absent)
    NOT_FOUND = "not_found"  # Non-200 status
    PROTECTED = "protected"  # 401/403
    SERVER_ERROR = "server_error"  # 5xx
    NETWORK_ERROR = "network_error"  # Request failed


class SPAType(StrEnum):
    """Detected SPA framework type (or traditional server)."""

    ANGULAR = "angular"
    REACT = "react"
    VUE = "vue"
    NEXTJS = "nextjs"
    GENERIC_SPA = "generic_spa"
    TRADITIONAL_SERVER = "traditional_server"
    UNKNOWN = "unknown"


# ──────────────────────────────────────────────────────────────────────────
# Probe categorization (Gap 5) + 4-level display severity (Gap 2)
# ──────────────────────────────────────────────────────────────────────────
# Both are display-layer metadata only — they never gate the verdict, which
# stays driven by (http_status, content_type, body signature, shell
# fingerprint) alone and is therefore framework-agnostic.

# Well-known static files every site is expected to serve.
_STATIC_PROBE_NAMES: frozenset[str] = frozenset(
    {
        "robots.txt",
        "sitemap.xml",
        "sitemap_index.xml",
        "favicon.ico",
        "ads.txt",
        "security.txt",
        "llms.txt",
        "humans.txt",
    }
)

# Route-like probes: admin panels, auth pages, debug UIs, API roots.
_ROUTE_PROBE_MARKERS: tuple[str, ...] = (
    "admin",
    "login",
    "signin",
    "signup",
    "register",
    "logout",
    "auth",
    "oauth",
    "token",
    "callback",
    "debug",
    "console",
    "manager",
    "cpanel",
    "plesk",
    "graphql",
    "graphiql",
    "playground",
    "api",
    "swagger-ui",
    "redoc",
    "status",
    "health",
    "metrics",
    "prometheus",
    "actuator",
)


def categorize_probe(path: str) -> str:
    """Classify a probe path into a display category (Gap 5).

    Returns one of ``Security/File`` (sensitive dotfiles / config files),
    ``Security/Route`` (admin/auth/debug/API routes) or ``Security/Static``
    (well-known static files like robots.txt / sitemap.xml).
    """
    lowered = path.lower()
    name = lowered.rstrip("/").rsplit("/", 1)[-1]
    if name in _STATIC_PROBE_NAMES:
        return "Security/Static"
    # HTML-OK paths (admin/auth/debug/docs UIs) are route probes by design
    if _is_html_ok_path(path):
        return "Security/Route"
    if name in ("v1", "v2", "v3") or any(m in lowered for m in _ROUTE_PROBE_MARKERS):
        return "Security/Route"
    return "Security/File"


def _display_severity_for_path(path: str) -> str:
    """4-level display severity for a probe path (Gap 2, display only).

    Returns one of ``critical`` / ``high`` / ``medium`` / ``low``. This is
    purely for the report's probe-results table — the per-path
    ``IssueSeverity`` in ``SECURITY_PROBE_PATHS`` still governs Issue rows.
    """
    lowered = path.lower()

    # critical — credential / secret material
    if (
        "/.env" in lowered
        or lowered.endswith(".env")
        or "/.git/" in lowered
        or ("id_rsa" in lowered and not lowered.endswith(".pub"))
        or "/.aws/credentials" in lowered
        or "wp-config" in lowered
        or "/.htpasswd" in lowered
    ):
        return "critical"

    # high — admin/debug panels, phpinfo, config/backup dumps, VCS metadata
    if any(
        m in lowered
        for m in (
            "admin",
            "debug",
            "phpinfo",
            "/config",
            "configuration",
            "settings.json",
            "backup",
            ".sql",
            "/.htaccess",
            "web.config",
            "/credentials",
            "/secret",
            "actuator/env",
            "actuator/heapdump",
            "actuator/configprops",
            "composer",
            "docker-compose",
            "/.svn/",
            "/.hg/",
            "/cvs/",
            "manager",
            "console",
            "cpanel",
            "plesk",
            "server-info",
            "server-status",
            "info.php",
        )
    ):
        return "high"

    # medium — API / GraphQL enumeration surface
    if any(
        m in lowered
        for m in (
            "graphql",
            "graphiql",
            "playground",
            "/api",
            "swagger",
            "openapi",
            "/metrics",
            "/prometheus",
            "actuator",
            "redoc",
            "/v1",
            "/v2",
            "/v3",
        )
    ):
        return "medium"

    # low — everything else (static files, auth pages, info-only paths)
    return "low"


# Map of probe path → 4-level display severity (critical/high/medium/low).
SEVERITY_MAP: dict[str, str] = {path: _display_severity_for_path(path) for path, _, _ in SECURITY_PROBE_PATHS}


# Paths that legitimately return text/html (admin panels, auth pages, docs UIs).
# These should NOT be rejected by the Content-Type check.
_HTML_OK_PATHS: set[str] = {
    "/admin",
    "/admin/",
    "/administrator",
    "/internal/admin",
    "/wp-admin",
    "/wp-admin/",
    "/wp-login.php",
    "/admin.php",
    "/manager",
    "/manager/html",
    "/console",
    "/_admin",
    "/cpanel",
    "/plesk",
    "/debug",
    "/debug/",
    "/_debug",
    "/__debug__",
    "/swagger-ui",
    "/swagger-ui.html",
    "/redoc",
    "/graphiql",
    "/playground",
    "/login",
    "/signin",
    "/signup",
    "/register",
    "/logout",
    "/auth",
    "/oauth",
    "/callback",
    "/api-docs",
    "/server-info",
    "/server-status",
    "/phpinfo.php",
    "/info.php",
    "/__nextjs_original-stack-frame",
}


def _is_html_ok_path(path: str) -> bool:
    """Return True if this path legitimately returns HTML (admin/auth/docs UI)."""
    return path in _HTML_OK_PATHS or path.rstrip("/") in _HTML_OK_PATHS


# Angular CLI hashed-bundle pattern, e.g. /polyfills.a5856ccaff510c09debf.js
_ANGULAR_BUNDLE_RE = re.compile(r"/(?:polyfills|vendor|app|main|runtime)\.[a-f0-9]{8,}\.(?:js|css)")


# ──────────────────────────────────────────────────────────────────────────
# Fix 4 — SPA Detection Before Probing
# ──────────────────────────────────────────────────────────────────────────


def detect_spa(client: httpx.Client, base_url: str, timeout: float = 10.0) -> tuple[SPAType, str]:
    """Detect if the target is a Single Page Application.

    Requests a clearly non-existent path and checks the response. If the
    server returns HTTP 200 with ``text/html`` for a path that definitely
    doesn't exist, the target is a SPA with catch-all routing.

    Returns ``(spa_type, shell_body)`` — the detected SPA framework type
    (or ``TRADITIONAL_SERVER`` if the server correctly returns 404 for
    non-existent paths) plus the response body of the catch-all fallback
    (``""`` when not applicable). The shell body acts as a soft-404
    fingerprint: any later probe response that matches it is the SPA
    shell — an effective "404 SPA Error" — not a real route or file.
    """
    test_path = "/THIS_PATH_ABSOLUTELY_DOES_NOT_EXIST_cbtp_probe_xyz123"
    url = urljoin(base_url.rstrip("/") + "/", test_path.lstrip("/"))
    try:
        resp = client.get(url, timeout=timeout, follow_redirects=True)
    except httpx.RequestError:
        return SPAType.UNKNOWN, ""

    if resp.status_code == 404:
        return SPAType.TRADITIONAL_SERVER, ""

    if resp.status_code == 200:
        content_type = resp.headers.get("Content-Type", "")
        body = resp.text
        body_lower = body[:500].lower()

        if "text/html" not in content_type:
            return SPAType.TRADITIONAL_SERVER, ""

        # Framework-specific markers (display labels only — verdicts never
        # depend on these). Angular CLI apps may use a custom root selector
        # (e.g. <my-app>), so also match the hashed-bundle build pattern
        # (polyfills|vendor|app|main|runtime).<hash>.js|css together with
        # the <base href> Angular CLI emits.
        if (
            "<app-root" in body_lower
            or "ng-version" in body_lower
            or ("<base href" in body_lower and _ANGULAR_BUNDLE_RE.search(body_lower))
        ):
            return SPAType.ANGULAR, body
        if "__next" in body_lower or "_next/static" in body_lower:
            return SPAType.NEXTJS, body
        if 'id="root"' in body_lower:
            return SPAType.REACT, body
        if 'id="app"' in body_lower and "vue" in body_lower:
            return SPAType.VUE, body
        if '<div id="app">' in body_lower:
            return SPAType.VUE, body
        # Generic SPA: 200 + HTML for a non-existent path
        return SPAType.GENERIC_SPA, body

    return SPAType.UNKNOWN, ""


def _is_spa_shell_body(shell_body: str, body: str) -> bool:
    """Return True if ``body`` is the SPA catch-all shell (soft-404).

    Compares ``body`` against the shell captured by :func:`detect_spa`:
    exact match first, then a high-similarity fuzzy match to tolerate
    small dynamic differences in the shell (nonces, timestamps, CSRF
    tokens injected per-request).
    """
    if not shell_body or not body:
        return False
    if shell_body == body:
        return True
    # Cheap length gate before the O(n*m) fuzzy comparison — a shell and a
    # genuinely different page differ in size by far more than 20%.
    longer = max(len(shell_body), len(body))
    if abs(len(shell_body) - len(body)) > longer * 0.2:
        return False
    return difflib.SequenceMatcher(None, shell_body, body).ratio() >= 0.90


# ──────────────────────────────────────────────────────────────────────────
# Fix 2 — Body Signature Validation
# ──────────────────────────────────────────────────────────────────────────


def validate_body(path: str, body: str) -> tuple[bool, str]:
    """Validate that the response body matches the expected file format.

    Returns ``(True, reason)`` if the body looks like a real file of the
    expected type, ``(False, reason)`` if it looks like an SPA HTML shell
    or doesn't match the expected format.
    """
    body_lower = body[:200].lower().lstrip()

    # Paths that legitimately return HTML (admin panels, auth pages, docs
    # UIs) — HTML is the EXPECTED format here, so accept any non-empty
    # body. The SPA-shell case for these paths is eliminated upstream by
    # the shell comparison in _probe_one (Fix 1b), not by this function.
    if _is_html_ok_path(path):
        if len(body.strip()) > 0:
            return True, "HTML page (expected for this path type)"
        return False, "Empty response"

    # Universal SPA check — if body starts with HTML doctype/html tag, it's a fallback
    if body_lower.startswith("<!doctype html") or body_lower.startswith("<html"):
        return False, "SPA HTML shell — not a real file"

    # .env files should contain KEY=VALUE pairs
    if path.endswith(".env") or "/.env" in path:
        if re.search(r"^[A-Z_][A-Z0-9_]*=.+", body, re.MULTILINE):
            return True, "REAL .env — contains KEY=VALUE pairs"
        return False, "No KEY=VALUE pattern found — SPA fallback"

    # .git/config should start with [core]
    if "/.git/config" in path:
        if body.lstrip().startswith("[core]") or "[remote" in body:
            return True, "REAL git config"
        return False, "No git config header found — SPA fallback"

    # .git/HEAD should contain a ref pointer
    if "/.git/head" in path:
        if body.strip().startswith("ref:") or re.match(r"^[0-9a-f]{40}$", body.strip()):
            return True, "REAL git HEAD ref"
        return False, "No git HEAD ref found — SPA fallback"

    # .git/index is binary — check for non-HTML binary content
    if "/.git/index" in path:
        if "<html" not in body_lower and "<!doctype" not in body_lower:
            return True, "REAL git index (binary)"
        return False, "HTML response — SPA fallback"

    # SQL dumps should contain SQL keywords
    if path.endswith(".sql"):
        body_upper = body.upper()
        if any(kw in body_upper for kw in ["INSERT INTO", "CREATE TABLE", "DROP TABLE", "SET NAMES"]):
            return True, "REAL SQL dump — contains SQL statements"
        return False, "No SQL statements found — SPA fallback"

    # JSON config files should parse as valid JSON
    if path.endswith(".json"):
        try:
            json.loads(body)
            return True, "REAL JSON file — valid JSON"
        except (json.JSONDecodeError, ValueError):
            return False, "Body is not valid JSON — SPA fallback"

    # YAML files should contain YAML structure (key: value)
    if path.endswith((".yml", ".yaml")):
        if re.search(r"^\s*\w[\w-]*\s*:", body, re.MULTILINE) and "<html" not in body_lower:
            return True, "REAL YAML file — contains key: value structure"
        return False, "Body does not look like YAML — SPA fallback"

    # SSH private key
    if "id_rsa" in path and not path.endswith(".pub"):
        if "-----BEGIN" in body and "PRIVATE KEY" in body:
            return True, "REAL SSH private key — PEM header found"
        return False, "No PEM key header found — SPA fallback"

    # SSH public key
    if "id_rsa.pub" in path:
        if re.match(r"^ssh-(rsa|ed25519|dsa|ecdsa)\s+", body.strip()):
            return True, "REAL SSH public key"
        return False, "No SSH public key format found — SPA fallback"

    # .ssh/known_hosts
    if "known_hosts" in path:
        if re.search(r"^\S+\s+ssh-", body, re.MULTILINE):
            return True, "REAL known_hosts file"
        return False, "No known_hosts format found — SPA fallback"

    # AWS credentials
    if ".aws/credentials" in path:
        if "[default]" in body or "aws_access_key_id" in body:
            return True, "REAL AWS credentials file"
        return False, "No AWS credential format found — SPA fallback"

    # AWS config
    if ".aws/config" in path:
        if "[default]" in body or "aws_" in body:
            return True, "REAL AWS config file"
        return False, "No AWS config format found — SPA fallback"

    # .htpasswd should contain hashed credentials
    if ".htpasswd" in path:
        if re.search(r"^\S+:\$", body, re.MULTILINE):
            return True, "REAL .htpasswd — contains hashed credentials"
        return False, "No htpasswd format found — SPA fallback"

    # .htaccess should contain Apache directives
    if ".htaccess" in path:
        if any(d in body for d in ["Rewrite", "Auth", "Deny", "Allow", "Options"]):
            return True, "REAL .htaccess — contains Apache directives"
        return False, "No Apache directives found — SPA fallback"

    # web.config should contain XML config
    if "web.config" in path:
        if "<?xml" in body and "<configuration" in body_lower:
            return True, "REAL web.config — XML configuration"
        return False, "No XML configuration found — SPA fallback"

    # .DS_Store is binary
    if ".ds_store" in path:
        if "<html" not in body_lower and "<!doctype" not in body_lower:
            return True, "REAL .DS_Store (binary)"
        return False, "HTML response — SPA fallback"

    # composer.json / package.json — already handled by .json above
    # Gemfile should contain Ruby gem declarations
    if path.endswith("/gemfile") or path == "/gemfile":
        if "source" in body_lower and "gem" in body_lower:
            return True, "REAL Gemfile — contains gem declarations"
        return False, "No Gemfile format found — SPA fallback"

    # requirements.txt should contain pip package lines
    if "requirements.txt" in path:
        if re.search(r"^[A-Za-z0-9_\[\]>=<.\-]+\s*(==|>=|<=|~=|!=|;|$)", body, re.MULTILINE):
            return True, "REAL requirements.txt — contains pip packages"
        return False, "No pip package format found — SPA fallback"

    # Pipfile should contain [packages] or [dev-packages]
    if "pipfile" in path and not path.endswith(".lock"):
        if "[packages]" in body or "[dev-packages]" in body:
            return True, "REAL Pipfile — contains package sections"
        return False, "No Pipfile sections found — SPA fallback"

    # Dockerfile should contain Docker instructions
    if path.endswith("/dockerfile") or path == "/dockerfile":
        if any(d in body for d in ["FROM ", "RUN ", "COPY ", "WORKDIR ", "CMD ", "ENTRYPOINT"]):
            return True, "REAL Dockerfile — contains Docker instructions"
        return False, "No Docker instructions found — SPA fallback"

    # docker-compose files — handled by .yml/.yaml above, but check for services:
    if "docker-compose" in path:
        if "services" in body_lower and "<html" not in body_lower:
            return True, "REAL docker-compose file — contains services"
        return False, "No docker-compose format found — SPA fallback"

    # .gitignore should contain ignore patterns
    if ".gitignore" in path:
        if "<html" not in body_lower and "<!doctype" not in body_lower and len(body.strip()) > 0:
            return True, "REAL .gitignore — contains ignore patterns"
        return False, "HTML response — SPA fallback"

    # .dockerignore similar to .gitignore
    if ".dockerignore" in path:
        if "<html" not in body_lower and "<!doctype" not in body_lower and len(body.strip()) > 0:
            return True, "REAL .dockerignore — contains ignore patterns"
        return False, "HTML response — SPA fallback"

    # robots.txt should contain User-agent/Disallow directives
    if "robots.txt" in path:
        if "user-agent" in body_lower or "disallow" in body_lower or "allow:" in body_lower:
            return True, "REAL robots.txt — contains crawler directives"
        return False, "No robots.txt directives found — SPA fallback"

    # sitemap.xml should contain XML sitemap
    if "sitemap" in path and path.endswith(".xml"):
        if ("<?xml" in body and "<urlset" in body_lower) or "<sitemapindex" in body_lower:
            return True, "REAL sitemap.xml — contains sitemap XML"
        return False, "No sitemap XML found — SPA fallback"

    # security.txt
    if "security.txt" in path:
        if "contact:" in body_lower or "expires:" in body_lower:
            return True, "REAL security.txt — contains security contact info"
        return False, "No security.txt format found — SPA fallback"

    # humans.txt
    if "humans.txt" in path:
        if "<html" not in body_lower and "<!doctype" not in body_lower and len(body.strip()) > 0:
            return True, "REAL humans.txt"
        return False, "HTML response — SPA fallback"

    # ads.txt
    if "ads.txt" in path:
        if re.search(r"^\s*\w+,\s*\w+", body, re.MULTILINE):
            return True, "REAL ads.txt — contains ad network entries"
        return False, "No ads.txt format found — SPA fallback"

    # CHANGELOG.md / README.md / LICENSE — text files, just not HTML
    if path.endswith((".md", ".txt")) or path in (
        "/license",
        "/version",
        "/changelog.md",
        "/readme.md",
        "/readme.txt",
        "/llms.txt",
    ):
        if "<html" not in body_lower and "<!doctype" not in body_lower and len(body.strip()) > 0:
            return True, "REAL text file"
        return False, "HTML response — SPA fallback"

    # VCS metadata (.svn, .hg, CVS)
    if "/.svn/" in path or "/.hg/" in path or "/cvs/" in path:
        if "<html" not in body_lower and "<!doctype" not in body_lower:
            return True, "REAL VCS metadata (non-HTML)"
        return False, "HTML response — SPA fallback"

    # backup archives — binary, check for zip/tar headers
    if path.endswith((".zip", ".tar.gz", ".tgz", ".tar")):
        if "<html" not in body_lower and "<!doctype" not in body_lower:
            return True, "REAL backup archive (binary)"
        return False, "HTML response — SPA fallback"

    # backup directory / config / credentials / secret — generic non-HTML check
    if path in ("/backup", "/config", "/configuration", "/credentials", "/secret"):
        if "<html" not in body_lower and "<!doctype" not in body_lower and len(body.strip()) > 0:
            return True, "REAL file (non-HTML content)"
        return False, "HTML response — SPA fallback"

    # Actuator endpoints should return JSON
    if "/actuator/" in path:
        if "application/json" in body[:0] or body.strip().startswith("{"):
            try:
                json.loads(body)
                return True, "REAL actuator response — valid JSON"
            except (json.JSONDecodeError, ValueError):
                pass
        return False, "No valid JSON actuator response — SPA fallback"

    # GraphQL endpoint should return JSON
    if path == "/graphql":
        if body.strip().startswith("{") or "graphql" in body_lower:
            return True, "REAL GraphQL endpoint"
        return False, "No GraphQL response — SPA fallback"

    # API roots should return JSON
    if path in ("/api/v1", "/api/v2", "/api/v3", "/v1", "/v2", "/api/health"):
        if body.strip().startswith("{") or body.strip().startswith("["):
            try:
                json.loads(body)
                return True, "REAL API response — valid JSON"
            except (json.JSONDecodeError, ValueError):
                pass
        # API health might return plain text "healthy" etc.
        if "<html" not in body_lower and "<!doctype" not in body_lower and len(body.strip()) > 0:
            return True, "REAL API response (text)"
        return False, "HTML response — SPA fallback"

    # OpenAPI specs should be JSON or YAML
    if "openapi" in path or "swagger" in path:
        if path.endswith(".json"):
            try:
                json.loads(body)
                return True, "REAL OpenAPI spec — valid JSON"
            except (json.JSONDecodeError, ValueError):
                pass
        if (path.endswith(".yaml") or path.endswith(".yml")) and ("openapi:" in body_lower or "swagger:" in body_lower):
            return True, "REAL OpenAPI spec — YAML"
        if body.strip().startswith("{") and "openapi" in body_lower:
            return True, "REAL OpenAPI spec — JSON"
        return False, "No OpenAPI spec format found — SPA fallback"

    # favicon.ico — binary image
    if "favicon.ico" in path:
        if "<html" not in body_lower and "<!doctype" not in body_lower:
            return True, "REAL favicon (binary)"
        return False, "HTML response — SPA fallback"

    # crossdomain.xml / clientaccesspolicy.xml — XML policy files
    if path.endswith(".xml") and ("crossdomain" in path or "clientaccess" in path):
        if "<?xml" in body:
            return True, "REAL XML policy file"
        return False, "No XML found — SPA fallback"

    # Unknown path type — reject HTML
    if "<html" in body_lower or "<!doctype" in body_lower:
        return False, "HTML response — likely SPA fallback"
    if len(body.strip()) > 0:
        return True, "Non-HTML response — may be real"
    return False, "Empty response"


# ──────────────────────────────────────────────────────────────────────────
# Fix 3 — Size Fingerprint Check
# ──────────────────────────────────────────────────────────────────────────

# SPA shell size range (Angular/React/Vue index.html is typically 2–20 KB)
_SPA_SHELL_MIN = 2000  # 2 KB
_SPA_SHELL_MAX = 20000  # 20 KB


def size_fingerprint(path: str, content_size: int) -> str:
    """Check if the response size matches a known SPA shell size range.

    Returns one of:
    - ``"POSSIBLE_SPA_SHELL"`` — size is in the SPA shell range (2–20 KB)
    - ``"SMALL_FILE"`` — size is very small, may be a real small file
    - ``"LARGE_FILE"`` — size is large, likely a real file (e.g. SQL dump)
    - ``"INCONCLUSIVE"`` — size doesn't match any fingerprint
    """
    if _SPA_SHELL_MIN <= content_size <= _SPA_SHELL_MAX:
        return "POSSIBLE_SPA_SHELL"
    if content_size < 500:
        return "SMALL_FILE"
    if content_size > 100_000:
        return "LARGE_FILE"
    return "INCONCLUSIVE"


@dataclass
class ProbeResult:
    """Result of probing a single path."""

    path: str
    url: str
    status_code: int | None
    exposed: bool  # True if 2xx AND confirmed real (not SPA fallback)
    protected: bool  # True if 401/403
    severity: IssueSeverity
    description: str
    error: str | None = None
    verdict: ProbeVerdict = ProbeVerdict.NOT_FOUND
    content_type: str = ""
    body_size: int = 0
    validation_reason: str = ""  # Why it was confirmed or rejected
    category: str = ""  # Security/File | Security/Route | Security/Static (Gap 5)
    display_severity: str = ""  # critical | high | medium | low (Gap 2, display only)

    def __post_init__(self) -> None:
        if not self.category:
            self.category = categorize_probe(self.path)
        if not self.display_severity:
            self.display_severity = SEVERITY_MAP.get(self.path, "low")


def _probe_one(
    client: httpx.Client,
    base_url: str,
    path: str,
    severity: IssueSeverity,
    description: str,
    timeout: float = 10.0,
    spa_mode: bool = False,
    spa_shell: str = "",
) -> ProbeResult:
    """Probe a single path with SPA false-positive elimination.

    When ``spa_mode`` is True (target detected as SPA), uses GET instead of
    HEAD and applies the full validation pipeline:
    1. Content-Type check — reject ``text/html`` for non-HTML paths
    2. Shell comparison — reject bodies matching the SPA catch-all shell
       (``spa_shell`` from :func:`detect_spa`), even for HTML-OK paths
    3. Body signature validation — verify body matches expected file format
    4. Size fingerprint — flag SPA shell-sized responses

    When ``spa_mode`` is False (traditional server), uses HEAD (fallback to
    GET) and trusts the status code directly — no body validation needed.
    """
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    error: str | None = None
    status_code: int | None = None
    content_type: str = ""
    body: str = ""
    body_size: int = 0

    try:
        if spa_mode:
            # SPA mode: always use GET to inspect the body for validation
            resp = client.get(url, timeout=timeout, follow_redirects=True)
        else:
            # Traditional server: try HEAD first (lighter); fall back to GET
            resp = client.head(url, timeout=timeout, follow_redirects=True)
            if resp.status_code in (405, 404):
                resp = client.get(url, timeout=timeout, follow_redirects=True)

        status_code = resp.status_code
        content_type = resp.headers.get("Content-Type", "")
        body = resp.text
        body_size = len(resp.content)
    except httpx.RequestError as e:
        return ProbeResult(
            path=path,
            url=url,
            status_code=None,
            exposed=False,
            protected=False,
            severity=severity,
            description=description,
            error=f"{type(e).__name__}: {e}",
            verdict=ProbeVerdict.NETWORK_ERROR,
        )
    except Exception as e:  # pragma: no cover — defensive
        return ProbeResult(
            path=path,
            url=url,
            status_code=None,
            exposed=False,
            protected=False,
            severity=severity,
            description=description,
            error=f"{type(e).__name__}: {e}",
            verdict=ProbeVerdict.NETWORK_ERROR,
        )

    # ── Step 1: Non-200 = NOT_FOUND or SERVER_ERROR ──
    if status_code is None:
        return ProbeResult(
            path=path,
            url=url,
            status_code=None,
            exposed=False,
            protected=False,
            severity=severity,
            description=description,
            error=error,
            verdict=ProbeVerdict.NETWORK_ERROR,
        )

    if 500 <= status_code < 600:
        return ProbeResult(
            path=path,
            url=url,
            status_code=status_code,
            exposed=False,
            protected=False,
            severity=severity,
            description=description,
            verdict=ProbeVerdict.SERVER_ERROR,
            content_type=content_type,
            body_size=body_size,
        )

    if status_code in (401, 403):
        return ProbeResult(
            path=path,
            url=url,
            status_code=status_code,
            exposed=False,
            protected=True,
            severity=severity,
            description=description,
            verdict=ProbeVerdict.PROTECTED,
            content_type=content_type,
            body_size=body_size,
        )

    if not (200 <= status_code < 300):
        return ProbeResult(
            path=path,
            url=url,
            status_code=status_code,
            exposed=False,
            protected=False,
            severity=severity,
            description=description,
            verdict=ProbeVerdict.NOT_FOUND,
            content_type=content_type,
            body_size=body_size,
        )

    # ── Status is 2xx — now validate to eliminate SPA false positives ──

    # If not SPA mode, trust the status code (traditional server returns
    # real 404s for non-existent paths, so a 200 means the file exists).
    if not spa_mode:
        return ProbeResult(
            path=path,
            url=url,
            status_code=status_code,
            exposed=True,
            protected=False,
            severity=severity,
            description=description,
            verdict=ProbeVerdict.CONFIRMED_EXPOSED,
            content_type=content_type,
            body_size=body_size,
            validation_reason="Traditional server — 200 indicates real file",
        )

    # ── SPA mode: apply full validation pipeline ──

    # Fix 1 — Content-Type check: reject text/html for non-HTML paths
    if "text/html" in content_type and not _is_html_ok_path(path):
        return ProbeResult(
            path=path,
            url=url,
            status_code=status_code,
            exposed=False,
            protected=False,
            severity=severity,
            description=description,
            verdict=ProbeVerdict.FALSE_POSITIVE,
            content_type=content_type,
            body_size=body_size,
            validation_reason="Content-Type is text/html — SPA shell returned instead of real file",
        )

    # Fix 1b — Shell comparison (soft-404 fingerprint): HTML-OK paths
    # (/admin, /login, /debug, …) pass the Content-Type gate, but on a SPA
    # the server still returns the catch-all shell for them — the route
    # only exists client-side (or not at all). If the body matches the
    # shell captured by detect_spa, this is an effective "404 SPA Error",
    # NOT an exposed route.
    if _is_spa_shell_body(spa_shell, body):
        # Route probes (admin/login/debug UIs — HTML is valid for them) get
        # the dedicated SPA_FALLBACK verdict; file probes returning the SPA
        # shell stay FALSE_POSITIVE (master prompt semantics).
        is_route = categorize_probe(path) == "Security/Route"
        return ProbeResult(
            path=path,
            url=url,
            status_code=status_code,
            exposed=False,
            protected=False,
            severity=severity,
            description=description,
            verdict=ProbeVerdict.SPA_FALLBACK if is_route else ProbeVerdict.FALSE_POSITIVE,
            content_type=content_type,
            body_size=body_size,
            validation_reason=(
                "Body matches SPA catch-all shell — effective 404 SPA Error "
                "(HTTP 200 shell, route not exposed server-side)"
            ),
        )

    # Fix 2 — Body signature validation
    is_real, reason = validate_body(path, body)
    if not is_real:
        return ProbeResult(
            path=path,
            url=url,
            status_code=status_code,
            exposed=False,
            protected=False,
            severity=severity,
            description=description,
            verdict=ProbeVerdict.FALSE_POSITIVE,
            content_type=content_type,
            body_size=body_size,
            validation_reason=reason,
        )

    # Fix 3 — Size fingerprint check (advisory — doesn't override body validation
    # but adds context). If body passed validation but size matches SPA shell,
    # still treat as confirmed (body validation is more reliable than size).
    size_verdict = size_fingerprint(path, body_size)

    # ── Passed all checks — this is a real finding ──
    return ProbeResult(
        path=path,
        url=url,
        status_code=status_code,
        exposed=True,
        protected=False,
        severity=severity,
        description=description,
        verdict=ProbeVerdict.CONFIRMED_EXPOSED,
        content_type=content_type,
        body_size=body_size,
        validation_reason=f"{reason} (size: {size_verdict}, {body_size} bytes)",
    )


def run_security_probe(
    base_url: str,
    job: Job,
    db: Session,
    existing_fingerprints: set[str] | None = None,
) -> tuple[list[Issue], list[ProbeResult], str]:
    """Probe common security-relevant paths and persist findings as Issues.

    Implements SPA false-positive elimination per ``crawl_testing_full_analysis.md``:
    before probing, detects if the target is a SPA (catch-all routing that
    returns ``index.html`` with HTTP 200 for every path). If so, enables
    body validation mode — each 200 response is validated via Content-Type
    check, body signature validation, and size fingerprinting before being
    flagged as a real finding.

    Args:
        base_url: The target site root URL (e.g. ``https://example.com``).
        job: The active ``Job`` ORM instance (used for ``job.id``).
        db: SQLAlchemy session for persisting issues.
        existing_fingerprints: Fingerprints of issues already in the DB,
            used to avoid duplicates. If None, queried from the DB.

    Returns:
        A ``(issues, results, spa_type)`` tuple: the list of newly created
        ``Issue`` objects (empty if none found), the full list of
        ``ProbeResult`` objects for every probed path (including
        FALSE_POSITIVE / SPA_FALLBACK / NOT_FOUND verdicts, for the
        report's probe table), and the detected ``SPAType`` value string.
    """
    if existing_fingerprints is None:
        existing_fingerprints = {i.fingerprint for i in db.query(Issue).filter_by(job_id=job.id).all()}

    new_issues: list[Issue] = []
    all_results: list[ProbeResult] = []
    spa_type_value = SPAType.UNKNOWN.value
    headers = {
        "User-Agent": "CBTP-SecurityProbe/1.0 (cross-browser-testing-platform)",
    }

    try:
        with httpx.Client(headers=headers, verify=False) as client:
            # ── Fix 4: SPA Detection Before Probing ──
            spa_type, spa_shell = detect_spa(client, base_url)
            spa_type_value = spa_type.value
            spa_mode = spa_type not in (SPAType.TRADITIONAL_SERVER, SPAType.UNKNOWN)
            if spa_mode:
                logger.info(
                    f"Security probe: target detected as {spa_type.value} SPA — "
                    f"enabling body validation mode to eliminate false positives"
                )
            else:
                logger.info(
                    f"Security probe: target is {spa_type.value} — using standard probe mode (status-code based)"
                )

            false_positive_count = 0
            confirmed_count = 0

            for path, severity, description in SECURITY_PROBE_PATHS:
                try:
                    result = _probe_one(
                        client,
                        base_url,
                        path,
                        severity,
                        description,
                        spa_mode=spa_mode,
                        spa_shell=spa_shell,
                    )
                except Exception as e:  # pragma: no cover — defensive
                    logger.warning(f"Security probe for {path} failed (non-fatal): {e}")
                    continue

                all_results.append(result)

                # Log false positives at debug level for audit trail
                if result.verdict in (ProbeVerdict.FALSE_POSITIVE, ProbeVerdict.SPA_FALLBACK):
                    false_positive_count += 1
                    logger.debug(
                        f"Security probe: {path} → {result.verdict.value.upper()} "
                        f"(HTTP {result.status_code}, {result.validation_reason})"
                    )
                    continue

                # Only record findings for confirmed-exposed or server-error (5xx) paths.
                # FALSE_POSITIVE, SPA_FALLBACK, NOT_FOUND, PROTECTED, NETWORK_ERROR = skip.
                if result.verdict not in (ProbeVerdict.CONFIRMED_EXPOSED, ProbeVerdict.SERVER_ERROR):
                    continue

                fp = f"security_probe::{path}"
                if fp in existing_fingerprints:
                    continue

                if result.verdict == ProbeVerdict.CONFIRMED_EXPOSED:
                    status_note = "exposed"
                    # Fix 6: Only assign severity after confirming the file is real.
                    # Severity comes from the SECURITY_PROBE_PATHS table, which
                    # already maps each path to the correct severity.
                    issue_severity = severity
                    confirmed_count += 1
                else:  # SERVER_ERROR
                    status_note = f"server error {result.status_code}"
                    # Escalate server errors to FUNCTIONAL regardless of path severity.
                    issue_severity = IssueSeverity.FUNCTIONAL

                # Build a detailed error message including validation info
                error_detail = f"HTTP {result.status_code} — {status_note}"
                if result.validation_reason:
                    error_detail += f" | {result.validation_reason}"
                if result.content_type:
                    error_detail += f" | Content-Type: {result.content_type}"

                issue = Issue(
                    job_id=job.id,
                    fingerprint=fp,
                    title=f"SECURITY: {path} {status_note} (HTTP {result.status_code})",
                    description=description,
                    severity=issue_severity,
                    category=IssueCategory.SECURITY,
                    first_seen_url=result.url,
                    error_message=error_detail,
                    stack_trace="",
                    repro_steps=[
                        f"curl -I {result.url}",
                        f"Expected: 404 (absent) or 401/403 (protected). Got: {result.status_code}.",
                    ]
                    + ([f"Validation: {result.validation_reason}"] if result.validation_reason else []),
                    fix_status="unfixed",
                )
                db.add(issue)
                new_issues.append(issue)
                existing_fingerprints.add(fp)
                logger.info(
                    f"Security probe: {path} → HTTP {result.status_code} ({status_note}) — {result.verdict.value}"
                )
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(f"Security probe pass failed (non-fatal): {e}")

    if new_issues:
        db.commit()
        logger.info(
            f"Security probe complete: {len(new_issues)} confirmed findings "
            f"({sum(1 for i in new_issues if i.severity == IssueSeverity.BLOCKING)} blocking, "
            f"{sum(1 for i in new_issues if i.severity == IssueSeverity.FUNCTIONAL)} functional, "
            f"{sum(1 for i in new_issues if i.severity == IssueSeverity.ADVISORY)} advisory)"
            + (
                f" | {false_positive_count} false positives eliminated (SPA validation)"
                if spa_mode and false_positive_count > 0
                else ""
            )
        )
    else:
        logger.info(
            "Security probe complete: no exposed common paths found"
            + (
                f" ({false_positive_count} false positives eliminated by SPA validation)"
                if spa_mode and false_positive_count > 0
                else ""
            )
        )

    return new_issues, all_results, spa_type_value
