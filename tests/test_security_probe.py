"""Tests for the security probe: categorization, verdicts, cross-site consistency.

All HTTP traffic is mocked via a FakeClient patched over ``httpx.Client`` —
no test in this module touches the network. The parametrized archetype
matrix is what guarantees DeepSeek-like verdict consistency on ANY site
type (traditional server, Angular SPA, unknown catch-all SPA, soft-404
server, auth-walled site, flaky host).
"""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.discovery.security_probe import (
    SECURITY_PROBE_PATHS,
    SEVERITY_MAP,
    ProbeResult,
    ProbeVerdict,
    categorize_probe,
    run_security_probe,
    validate_body,
)
from src.models.database import Base, IssueCategory, IssueSeverity, Job, JobStatus

# SQLite in-memory DB configuration (same pattern as tests/test_database.py)
SQLITE_URL = "sqlite:///:memory:"


@pytest.fixture
def db_session():
    engine = create_engine(SQLITE_URL)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def job(db_session):
    job = Job(
        id=uuid.uuid4(),
        url="https://target.test",
        scope="same-domain",
        status=JobStatus.RUNNING,
        browser_matrix={"chromium": True, "firefox": False, "webkit": False},
        max_pages=5,
        max_depth=2,
        max_time_seconds=60,
    )
    db_session.add(job)
    db_session.commit()
    return job


# ---------------------------------------------------------------------------
# Mock HTTP layer
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", content_type: str = "text/html"):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": content_type}


class FakeClient:
    """Minimal httpx.Client stand-in driven by a path → response router."""

    def __init__(self, router):
        self._router = router

    def get(self, url, timeout=10.0, follow_redirects=True):
        return self._router(urlparse(url).path)

    def head(self, url, timeout=10.0, follow_redirects=True):
        return self._router(urlparse(url).path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_client(monkeypatch, router) -> None:
    monkeypatch.setattr(
        "src.discovery.security_probe.httpx.Client",
        lambda *args, **kwargs: FakeClient(router),
    )


# ---------------------------------------------------------------------------
# Unit tests: categorize_probe / SEVERITY_MAP / validate_body / ProbeResult
# ---------------------------------------------------------------------------


def test_categorize_probe_mapping():
    assert categorize_probe("/.env") == "Security/File"
    assert categorize_probe("/.git/config") == "Security/File"
    assert categorize_probe("/id_rsa") == "Security/File"
    assert categorize_probe("/backup.sql") == "Security/File"
    assert categorize_probe("/robots.txt") == "Security/Static"
    assert categorize_probe("/sitemap.xml") == "Security/Static"
    assert categorize_probe("/.well-known/security.txt") == "Security/Static"
    assert categorize_probe("/favicon.ico") == "Security/Static"
    assert categorize_probe("/admin") == "Security/Route"
    assert categorize_probe("/wp-login.php") == "Security/Route"
    assert categorize_probe("/graphql") == "Security/Route"
    assert categorize_probe("/api/health") == "Security/Route"
    assert categorize_probe("/login") == "Security/Route"


def test_severity_map_levels():
    # critical — credential / secret material
    assert SEVERITY_MAP["/.env"] == "critical"
    assert SEVERITY_MAP["/.git/config"] == "critical"
    assert SEVERITY_MAP["/id_rsa"] == "critical"
    assert SEVERITY_MAP["/.aws/credentials"] == "critical"
    assert SEVERITY_MAP["/wp-config.php"] == "critical"
    # high — admin/debug panels, phpinfo, config dumps
    assert SEVERITY_MAP["/admin"] == "high"
    assert SEVERITY_MAP["/debug"] == "high"
    assert SEVERITY_MAP["/phpinfo.php"] == "high"
    assert SEVERITY_MAP["/config.json"] == "high"
    # medium — API / GraphQL enumeration
    assert SEVERITY_MAP["/graphql"] == "medium"
    assert SEVERITY_MAP["/api/v1"] == "medium"
    assert SEVERITY_MAP["/swagger.json"] == "medium"
    # low — static files, auth pages, info-only paths
    assert SEVERITY_MAP["/robots.txt"] == "low"
    assert SEVERITY_MAP["/sitemap.xml"] == "low"
    assert SEVERITY_MAP["/login"] == "low"
    # Every probed path is covered by the map
    assert set(SEVERITY_MAP) == {p for p, _, _ in SECURITY_PROBE_PATHS}


def test_validate_body_real_env_content():
    is_real, reason = validate_body("/.env", "DB_PASSWORD=secret\nAPI_KEY=abc123\n")
    assert is_real is True
    assert "KEY=VALUE" in reason


def test_validate_body_rejects_html_shell():
    shell = '<!doctype html><html><body><app-root ng-version="17.0.0"></app-root></body></html>'
    is_real, _ = validate_body("/.env", shell)
    assert is_real is False
    is_real, _ = validate_body("/.git/config", shell)
    assert is_real is False


def test_spa_fallback_verdict_member_exists():
    assert ProbeVerdict.SPA_FALLBACK.value == "spa_fallback"


def test_probe_result_post_init_fills_category_and_severity():
    result = ProbeResult(
        path="/.env",
        url="https://x.test/.env",
        status_code=200,
        exposed=False,
        protected=False,
        severity=IssueSeverity.BLOCKING,
        description="d",
        verdict=ProbeVerdict.FALSE_POSITIVE,
    )
    assert result.category == "Security/File"
    assert result.display_severity == "critical"

    route = ProbeResult(
        path="/admin",
        url="https://x.test/admin",
        status_code=200,
        exposed=False,
        protected=False,
        severity=IssueSeverity.FUNCTIONAL,
        description="d",
        verdict=ProbeVerdict.SPA_FALLBACK,
    )
    assert route.category == "Security/Route"
    assert route.display_severity == "high"


# ---------------------------------------------------------------------------
# Cross-site-type consistency matrix (6 archetypes, fully mocked HTTP)
# ---------------------------------------------------------------------------

ANGULAR_SHELL = (
    "<!doctype html><html><head><title>Buggy App</title></head>"
    '<body><app-root ng-version="17.0.0"></app-root>'
    '<script src="main.js"></script></body></html>'
)

GENERIC_SHELL = (
    "<!doctype html><html><head><title>Custom App</title></head>"
    '<body><div class="shell-container"><span>loading…</span></div></body></html>'
)

SOFT404_PAGE = (
    "<!doctype html><html><body><h1>Oops — page not found</h1><p>Custom error page with HTTP 200</p></body></html>"
)

REAL_ENV_BODY = "DB_PASSWORD=secret\nAPI_KEY=abc123\n"
REAL_ROBOTS_BODY = "User-agent: *\nDisallow: /admin\n"


def traditional_router(path: str) -> FakeResponse:
    if path == "/.env":
        return FakeResponse(200, REAL_ENV_BODY, "text/plain")
    return FakeResponse(404, "Not Found", "text/plain")


def angular_router(path: str) -> FakeResponse:
    return FakeResponse(200, ANGULAR_SHELL, "text/html; charset=utf-8")


def generic_spa_router(path: str) -> FakeResponse:
    return FakeResponse(200, GENERIC_SHELL, "text/html; charset=utf-8")


def soft404_router(path: str) -> FakeResponse:
    if path == "/robots.txt":
        return FakeResponse(200, REAL_ROBOTS_BODY, "text/plain")
    return FakeResponse(200, SOFT404_PAGE, "text/html")


def auth_walled_router(path: str) -> FakeResponse:
    return FakeResponse(403, "Forbidden", "text/plain")


def flaky_router(path: str) -> FakeResponse:
    raise httpx.ConnectError("connection reset by peer")


# (name, router, expected_spa_type, {path: expected_verdict}, expected_issue_count)
ARCHETYPES = [
    (
        "traditional server",
        traditional_router,
        "traditional_server",
        {
            "/.env": ProbeVerdict.CONFIRMED_EXPOSED,
            "/.git/config": ProbeVerdict.NOT_FOUND,
            "/admin": ProbeVerdict.NOT_FOUND,
            "/robots.txt": ProbeVerdict.NOT_FOUND,
        },
        1,
    ),
    (
        "Angular SPA",
        angular_router,
        "angular",
        {
            "/.env": ProbeVerdict.FALSE_POSITIVE,
            "/.git/config": ProbeVerdict.FALSE_POSITIVE,
            "/id_rsa": ProbeVerdict.FALSE_POSITIVE,
            "/admin": ProbeVerdict.SPA_FALLBACK,
            "/login": ProbeVerdict.SPA_FALLBACK,
        },
        0,
    ),
    (
        "generic/unknown catch-all SPA",
        generic_spa_router,
        "generic_spa",
        {
            "/.env": ProbeVerdict.FALSE_POSITIVE,
            "/.git/config": ProbeVerdict.FALSE_POSITIVE,
            "/admin": ProbeVerdict.SPA_FALLBACK,
            # /graphql expects JSON — an HTML shell is a file-style false positive
            "/graphql": ProbeVerdict.FALSE_POSITIVE,
        },
        0,
    ),
    (
        "soft-404 traditional server",
        soft404_router,
        "generic_spa",
        {
            "/.env": ProbeVerdict.FALSE_POSITIVE,
            "/admin": ProbeVerdict.SPA_FALLBACK,
            # robots.txt is an expected-public file: a 200 is BENIGN posture,
            # never an Issue (false-positive study fix — was CONFIRMED_EXPOSED).
            "/robots.txt": ProbeVerdict.BENIGN,
        },
        0,
    ),
    (
        "auth-walled site",
        auth_walled_router,
        "unknown",
        {
            "/.env": ProbeVerdict.PROTECTED,
            "/admin": ProbeVerdict.PROTECTED,
            "/robots.txt": ProbeVerdict.PROTECTED,
        },
        0,
    ),
    (
        "flaky site",
        flaky_router,
        "unknown",
        {
            "/.env": ProbeVerdict.NETWORK_ERROR,
            "/admin": ProbeVerdict.NETWORK_ERROR,
            "/robots.txt": ProbeVerdict.NETWORK_ERROR,
        },
        0,
    ),
]


@pytest.mark.parametrize(
    "name,router,expected_spa_type,expected_verdicts,expected_issue_count",
    ARCHETYPES,
    ids=[a[0] for a in ARCHETYPES],
)
def test_cross_site_verdict_consistency(
    monkeypatch,
    db_session,
    job,
    name,
    router,
    expected_spa_type,
    expected_verdicts,
    expected_issue_count,
):
    """Identical verdict semantics across every site archetype."""
    _patch_client(monkeypatch, router)

    issues, results, spa_type = run_security_probe(
        base_url=job.url,
        job=job,
        db=db_session,
    )

    assert spa_type == expected_spa_type
    assert len(issues) == expected_issue_count

    results_by_path = {r.path: r for r in results}
    # Every probed path produced a result with category + display severity
    assert len(results_by_path) == len(SECURITY_PROBE_PATHS)
    for r in results:
        assert r.category in ("Security/File", "Security/Route", "Security/Static")
        assert r.display_severity in ("critical", "high", "medium", "low")

    for path, verdict in expected_verdicts.items():
        assert results_by_path[path].verdict == verdict, (
            f"{name}: {path} should be {verdict.value}, "
            f"got {results_by_path[path].verdict.value} "
            f"({results_by_path[path].validation_reason})"
        )


def test_traditional_server_real_env_is_critical_blocking_issue(monkeypatch, db_session, job):
    """A real /.env body on a traditional server → CONFIRMED_EXPOSED, critical."""
    _patch_client(monkeypatch, traditional_router)

    issues, results, _spa_type = run_security_probe(
        base_url=job.url,
        job=job,
        db=db_session,
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == IssueSeverity.BLOCKING
    assert issue.category == IssueCategory.SECURITY
    assert "/.env" in issue.title

    env_result = {r.path: r for r in results}["/.env"]
    assert env_result.verdict == ProbeVerdict.CONFIRMED_EXPOSED
    assert env_result.display_severity == "critical"
    assert env_result.category == "Security/File"


def test_angular_spa_produces_no_security_issues(monkeypatch, db_session, job):
    """The buggy.justtestit.org archetype: shell 200s never become issues."""
    _patch_client(monkeypatch, angular_router)

    issues, results, spa_type = run_security_probe(
        base_url=job.url,
        job=job,
        db=db_session,
    )

    assert issues == []
    assert spa_type == "angular"
    false_positives = [r for r in results if r.verdict == ProbeVerdict.FALSE_POSITIVE]
    spa_fallbacks = [r for r in results if r.verdict == ProbeVerdict.SPA_FALLBACK]
    # SPA_FALLBACK is only ever assigned to route probes (HTML-OK paths whose
    # body matched the catch-all shell); non-HTML route probes (/graphql,
    # /api/health, …) and file probes land in FALSE_POSITIVE instead.
    assert all(r.category == "Security/Route" for r in spa_fallbacks)
    assert len(false_positives) > 0
    assert len(spa_fallbacks) > 0
