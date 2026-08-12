"""Tests for the HTML report generator's probe-intelligence sections.

Covers the summary stats grid (Gap 3), Key Findings (Gap 4), verdict badges
replacing "Broken (SPA 404)" (Gap 1), the Security Probe Results table
(Gaps 1/2/5), the health-score formula, and backward compatibility for old
jobs without the additive ``job.config`` keys.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.analysis.report_generator import (
    compute_health_score,
    generate_static_html_report,
)


class FakeRegistry:
    def __init__(self, endpoints):
        self._endpoints = endpoints

    def get_all(self):
        return self._endpoints


def _ep(url: str, status: int | None = 200, extra: dict | None = None, method: str = "GET"):
    return SimpleNamespace(
        url=url,
        method=method,
        last_status_code=status,
        status=SimpleNamespace(name="done"),
        extra=extra or {},
        snapshots={},
        a11y_report=None,
        performance_data=None,
        source=None,
        endpoint_type=None,
    )


def _job(config: dict | None = None):
    return SimpleNamespace(url="https://target.test", config=config or {})


def _render(tmp_path, monkeypatch, job, registry, issues=None) -> str:
    monkeypatch.chdir(tmp_path)
    path = generate_static_html_report("testjob123", job, registry, issues or [])
    with open(path, encoding="utf-8") as f:
        return f.read()


def _spa_config() -> dict:
    """buggy.justtestit.org-style config: Angular SPA, all probes false positive."""
    return {
        "spa_type": "angular",
        "spa_route_404s": [
            {
                "url": "https://target.test/admin",
                "message": "ERROR Error: Uncaught (in promise): Error: Cannot match any routes. URL Segment: 'admin'",
                "browser": "chromium",
            },
            {
                "url": "https://target.test/dead-route",
                "message": "ERROR Error: Uncaught (in promise): Error: Cannot match any routes. URL Segment: 'dead-route'",
                "browser": "chromium",
            },
        ],
        "security_probe_results": [
            {
                "path": "/.env",
                "url": "https://target.test/.env",
                "http_status": 200,
                "verdict": "false_positive",
                "category": "Security/File",
                "display_severity": "critical",
                "validation_reason": "Content-Type is text/html — SPA shell returned instead of real file",
                "content_type": "text/html; charset=utf-8",
                "body_size": 4096,
            },
            {
                "path": "/.git/config",
                "url": "https://target.test/.git/config",
                "http_status": 200,
                "verdict": "false_positive",
                "category": "Security/File",
                "display_severity": "critical",
                "validation_reason": "Content-Type is text/html — SPA shell returned instead of real file",
                "content_type": "text/html; charset=utf-8",
                "body_size": 4096,
            },
            {
                "path": "/admin",
                "url": "https://target.test/admin",
                "http_status": 200,
                "verdict": "spa_fallback",
                "category": "Security/Route",
                "display_severity": "high",
                "validation_reason": "Body matches SPA catch-all shell — effective 404 SPA Error",
                "content_type": "text/html; charset=utf-8",
                "body_size": 4096,
            },
            {
                "path": "/robots.txt",
                "url": "https://target.test/robots.txt",
                "http_status": 200,
                "verdict": "false_positive",
                "category": "Security/Static",
                "display_severity": "low",
                "validation_reason": "Content-Type is text/html — SPA shell returned instead of real file",
                "content_type": "text/html; charset=utf-8",
                "body_size": 4096,
            },
        ],
    }


def _spa_registry() -> FakeRegistry:
    return FakeRegistry(
        [
            _ep("https://target.test/"),
            _ep("https://target.test/login"),
            _ep("https://target.test/register"),
            _ep(
                "https://target.test/admin",
                extra={
                    "effective_status_code": "404",
                    "effective_status_reason": "SPA route error (angular)",
                    "effective_status_detail": "Client-side router could not match the requested path.",
                },
            ),
            _ep(
                "https://target.test/dead-route",
                extra={
                    "effective_status_code": "404",
                    "effective_status_reason": "SPA route error (angular)",
                },
            ),
        ]
    )


def test_summary_stats_grid_renders_seven_cards(tmp_path, monkeypatch):
    html = _render(tmp_path, monkeypatch, _job(_spa_config()), _spa_registry())
    for label in (
        "Total Tested",
        "OK (2xx)",
        "Redirects (3xx)",
        "Client Errors (4xx)",
        "Server Errors (5xx)",
        "Network Errors",
        "SPA Route 404s",
    ):
        assert label in html
    # Two distinct SPA-route-404 URLs were recorded
    assert ">2</div>" in html


def test_key_findings_mentions_platform(tmp_path, monkeypatch):
    html = _render(tmp_path, monkeypatch, _job(_spa_config()), _spa_registry())
    assert "Key Findings" in html
    assert "Angular SPA with client-side routing" in html
    assert "Token-based, registration open" in html
    assert "No confirmed file exposures" in html


def test_probe_path_rows_show_verdict_badge_not_broken_spa_404(tmp_path, monkeypatch):
    html = _render(tmp_path, monkeypatch, _job(_spa_config()), _spa_registry())
    # The old red label is gone entirely
    assert "Broken (SPA 404)" not in html
    # The /admin endpoint matched a stored probe result → its verdict badge
    assert "SPA FALLBACK" in html
    # Probe table rows carry the FALSE_POSITIVE verdict
    assert "FALSE POSITIVE" in html
    # Verdicts are rendered as badges with the semantic CSS classes
    assert "badge verdict-false-positive" in html
    assert "badge verdict-spa-fallback" in html


def test_probe_results_table_and_severity_badges(tmp_path, monkeypatch):
    config = _spa_config()
    config["security_probe_results"].append(
        {
            "path": "/.git/HEAD",
            "url": "https://target.test/.git/HEAD",
            "http_status": 200,
            "verdict": "confirmed_exposed",
            "category": "Security/File",
            "display_severity": "critical",
            "validation_reason": "REAL git HEAD ref",
            "content_type": "text/plain",
            "body_size": 41,
        }
    )
    html = _render(tmp_path, monkeypatch, _job(config), _spa_registry())
    assert "Security Probe Results" in html
    assert "Security/File" in html
    assert "Security/Route" in html
    assert "Security/Static" in html
    # Severity badge only rendered for the confirmed exposure
    assert "badge sev-critical" in html
    assert "CONFIRMED EXPOSED" in html
    # Recommendations: the exposure lands in Critical Security
    assert "Critical Security (1)" in html


def test_health_score_all_false_positive_is_100_A():
    registry = _spa_registry()
    grade, score = compute_health_score(registry, [])
    assert (grade, score) == ("A", 100)


def test_health_score_ignores_spa_fallback_endpoints():
    # An endpoint whose ONLY problem is a SPA route error never counts as broken
    ep = _ep("https://target.test/x", status=200, extra={"effective_status_code": "404"})
    grade, score = compute_health_score(FakeRegistry([ep]), [])
    assert (grade, score) == ("A", 100)


def test_real_http_error_still_counts_as_broken():
    registry = FakeRegistry(
        [
            _ep("https://target.test/"),
            _ep("https://target.test/missing", status=404),
        ]
    )
    _grade, score = compute_health_score(registry, [])
    assert score < 100


def test_old_job_without_config_keys_still_renders(tmp_path, monkeypatch):
    """Backward compat: no spa_type / probe results / spa_route_404s keys."""
    registry = FakeRegistry([_ep("https://target.test/")])
    html = _render(tmp_path, monkeypatch, _job({}), registry)
    # Key Findings degrades to N/A
    assert "Key Findings" in html
    assert "N/A" in html
    # Probe-dependent sections are omitted
    assert "Security Probe Results" not in html
    # Summary grid and health score still render
    assert "Total Tested" in html
    assert "Overall Health Score" in html
    # Results by Category falls back to registry-only rows
    assert "Results by Category" in html
    assert "Pages" in html


def test_spa_routing_recommendations_are_informational(tmp_path, monkeypatch):
    html = _render(tmp_path, monkeypatch, _job(_spa_config()), _spa_registry())
    assert "SPA Routing — informational (2)" in html
    assert "Angular router" in html


# ---------------------------------------------------------------------------
# HTTP-200 trap: organically-linked SPA route errors are broken internal
# links and must lower the health score; probe-only fallbacks stay exempt.
# ---------------------------------------------------------------------------


def _dom_linked_ep(url: str):
    """Endpoint discovered from the app's own DOM (e.g. href="/broken")
    whose client-side router crashed — HTTP 200 shell, effective 404."""
    ep = _ep(
        url,
        extra={
            "effective_status_code": "404",
            "effective_status_reason": "SPA route error (angular)",
        },
    )
    ep.source = SimpleNamespace(value="dom_link")
    return ep


def test_health_score_counts_organically_linked_spa_route_errors():
    registry = FakeRegistry(
        [
            _ep("https://target.test/"),
            _ep("https://target.test/login"),
            _ep("https://target.test/register"),
            _ep("https://target.test/about"),
            _dom_linked_ep("https://target.test/broken"),
        ]
    )
    grade, score = compute_health_score(registry, [])
    # 1 broken internal link out of 5 tested endpoints → 80
    assert (grade, score) == ("B", 80)


def test_health_score_probe_only_spa_fallback_still_exempt():
    # Wordlist/probe-discovered paths keep the false-positive exemption —
    # the app never links to them.
    ep = _ep("https://target.test/admin", extra={"effective_status_code": "404"})
    ep.source = SimpleNamespace(value="api_brute_force")
    grade, score = compute_health_score(FakeRegistry([ep]), [])
    assert (grade, score) == ("A", 100)


def test_health_score_counts_endpoint_also_found_via_dom_link():
    # First found by the wordlist, but ALSO linked from a real page — the
    # registry records extra["also_found_via_dom_link"], so it counts.
    ep = _ep(
        "https://target.test/admin",
        extra={
            "effective_status_code": "404",
            "also_found_via_dom_link": "https://target.test/",
        },
    )
    ep.source = SimpleNamespace(value="api_brute_force")
    grade, score = compute_health_score(FakeRegistry([ep]), [])
    assert (grade, score) == ("F", 0)


def test_health_score_ignores_untested_endpoints_in_denominator():
    registry = FakeRegistry(
        [
            _ep("https://target.test/"),  # tested, OK
            _ep("https://target.test/missing", status=404),  # tested, broken
            _ep("https://target.test/unvisited", status=None),  # never fetched
        ]
    )
    grade, score = compute_health_score(registry, [])
    # 1 broken of 2 TESTED endpoints (not of 3 discovered) → 50
    assert (grade, score) == ("F", 50)


def test_broken_internal_link_renders_badge_and_qa_recommendation(tmp_path, monkeypatch):
    ep = _dom_linked_ep("https://target.test/broken")
    config = _spa_config()
    config["spa_route_404s"] = [
        {
            "url": "https://target.test/broken",
            "message": "ERROR Error: Cannot match any routes. URL Segment: 'broken'",
            "browser": "chromium",
        }
    ]
    html = _render(tmp_path, monkeypatch, _job(config), FakeRegistry([ep]))
    # Red BROKEN LINK badge in the endpoints table
    assert "BROKEN LINK" in html
    assert "badge verdict-broken-link" in html
    # The link leaves the informational SPA bucket and lands in QA fixes
    assert "Fix broken internal link" in html
    assert "SPA Routing — informational (0)" in html
    # Key Findings flags the broken link instead of a benign SPA shell note
    assert "broken internal link(s)" in html
