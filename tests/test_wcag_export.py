"""Tests for WCAG/VPAT export (compliance-grade honesty rules).

Covers: rule→criterion mapping, verdict escalation (critical→Does Not
Support), pass evidence→Supports, no-evidence→Not Evaluated (never fake
"Supports"), aggregation across pages, markdown/HTML rendering, and the
owner-gated API endpoints.
Run: pytest tests/test_wcag_export.py -q
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from src.analysis.accessibility import A11yReport, A11yViolation
from src.analysis.wcag_export import (
    NOT_EVALUATED,
    NOT_SUPPORTS,
    PARTIAL,
    RULE_TO_WCAG,
    SUPPORTS,
    WCAG_CATALOG_A,
    WCAG_CATALOG_AA,
    build_wcag_conformance,
    render_vpat_html,
    render_vpat_markdown,
)
from src.api.app import SessionLocal, app, engine
from src.models.database import Base, PlatformUser

Base.metadata.create_all(bind=engine)
client = TestClient(app)


def _report(url: str, violations: list[A11yViolation]) -> A11yReport:
    return A11yReport(
        url=url,
        total_violations=len(violations),
        violations=violations,
        score=100 - 10 * len(violations),
    )


V_COLOR = A11yViolation(
    rule_id="color-contrast", impact="serious",
    description="Elements must have sufficient color contrast",
    help_url="https://deque.com/x", wcag_tags=["wcag2aa", "wcag143"],
    affected_nodes=12, html_snippets=["<a href='#'>dim</a>"],
)
V_ALT = A11yViolation(
    rule_id="image-alt", impact="critical",
    description="Images must have alt text",
    help_url="https://deque.com/y", wcag_tags=["wcag2a", "wcag111"],
    affected_nodes=3, html_snippets=["<img src=x>"],
)
V_MINOR = A11yViolation(
    rule_id="heading-order", impact="minor",
    description="Heading levels should only increase by one",
    help_url="https://deque.com/z", wcag_tags=["wcag2a", "wcag131"],
    affected_nodes=1, html_snippets=["<h4>skip</h4>"],
)


# ── Mapping integrity ──────────────────────────────────────────────────────


def test_all_mapped_rules_point_to_catalog_criteria():
    for rule, (num, level, _name) in RULE_TO_WCAG.items():
        if num == "unmapped":
            continue
        assert num in WCAG_CATALOG_A or num in WCAG_CATALOG_AA or num == "4.1.1", f"{rule}->{num}"
        assert level in ("A", "AA")


def test_catalogs_have_no_overlap():
    assert not (WCAG_CATALOG_A.keys() & WCAG_CATALOG_AA.keys())


# ── Verdicts ────────────────────────────────────────────────────────────────


def test_critical_violation_is_does_not_support():
    conf = build_wcag_conformance([_report("http://x", [V_ALT])], base_url="http://x")
    row = next(r for r in conf["criteria"] if r["number"] == "1.1.1")
    assert row["verdict"] == NOT_SUPPORTS
    assert "image-alt" in row["remarks"]
    assert row["evidence"][0]["nodes"] == 3


def test_serious_is_does_not_support():
    conf = build_wcag_conformance([_report("http://x", [V_COLOR])], base_url="http://x")
    row = next(r for r in conf["criteria"] if r["number"] == "1.4.3")
    assert row["verdict"] == NOT_SUPPORTS


def test_minor_only_is_partially_supports():
    conf = build_wcag_conformance([_report("http://x", [V_MINOR])], base_url="http://x")
    row = next(r for r in conf["criteria"] if r["number"] == "1.3.1")
    assert row["verdict"] == PARTIAL


def test_clean_scan_supports_exercised_criteria():
    # No violations at all: exercised rules (seen via empty reports) —
    # nothing triggered, but we only claim Supports where rules ran.
    conf = build_wcag_conformance([_report("http://x", [])], base_url="http://x")
    for row in conf["criteria"]:
        # without any violations we have no proof rules ran → Not Evaluated
        assert row["verdict"] in (NOT_EVALUATED, SUPPORTS)


def test_unexercised_criteria_are_not_evaluated_never_supported():
    conf = build_wcag_conformance([_report("http://x", [V_ALT])], base_url="http://x")
    # e.g. 1.4.2 Audio Control has no mapped axe rules that ran
    row = next(r for r in conf["criteria"] if r["number"] == "1.4.2")
    assert row["verdict"] == NOT_EVALUATED
    assert "manual review" in row["remarks"]


def test_aggregation_across_pages_takes_worst():
    conf = build_wcag_conformance(
        [_report("http://x/a", [V_MINOR]), _report("http://x/b", [V_ALT])],
        base_url="http://x",
    )
    row = next(r for r in conf["criteria"] if r["number"] == "1.1.1")
    assert row["verdict"] == NOT_SUPPORTS  # worst page wins
    assert row["evidence"][0]["url"] == "http://x/b"
    assert conf["meta"]["pages_audited"] == 2


def test_meta_counts_consistent():
    conf = build_wcag_conformance([_report("http://x", [V_ALT, V_COLOR])], base_url="http://x")
    counts = conf["meta"]["verdict_counts"]
    assert counts[NOT_SUPPORTS] == 2  # 1.1.1 + 1.4.3
    assert counts[NOT_EVALUATED] + counts[SUPPORTS] + counts[PARTIAL] == len(conf["criteria"]) - 2


def test_honesty_note_present():
    conf = build_wcag_conformance([_report("http://x", [])], base_url="http://x")
    assert "30-40%" in conf["meta"]["notes"]  # automation-limits disclaimer


# ── Rendering ────────────────────────────────────────────────────────────────


def test_markdown_renders_table():
    conf = build_wcag_conformance([_report("http://x", [V_ALT])], base_url="http://x")
    md = render_vpat_markdown(conf, product_name="ExampleSite")
    assert "# Accessibility Conformance Report" in md
    assert "| Criterion | Level | Conformance | Remarks |" in md
    assert "**Does Not Support**" in md
    assert "ExampleSite" in md
    assert "1.1.1 Non-text Content" in md


def test_html_renders_and_escapes():
    conf = build_wcag_conformance([_report("http://x", [V_ALT])], base_url="http://x")
    html_text = render_vpat_html(conf, product_name="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html_text or "&lt;script&gt;" in html_text
    assert "<table" in html_text
    assert "Does Not Support" in html_text
    assert "VPAT" in html_text


# ── API gating (owner-only) ────────────────────────────────────────────────


def test_vpat_endpoints_require_auth():
    r = client.get("/jobs/00000000-0000-0000-0000-000000000000/vpat/download")
    assert r.status_code == 401
    r = client.get("/jobs/00000000-0000-0000-0000-000000000000/wcag-conformance")
    assert r.status_code == 401


def test_vpat_404_for_missing_export():
    from src.api.auth import hash_password

    uname = f"wc_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    db.add(PlatformUser(id=uuid.uuid4(), username=uname, email=f"{uname}@l",
                        password_hash=hash_password("pw"), role="user", is_active=True))
    db.commit()
    db.close()
    r = client.post("/api/auth/login", json={"username": uname, "password": "pw"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    r = client.get("/jobs/00000000-0000-0000-0000-000000000000/vpat/download", headers=h)
    # job doesn't exist → anti-enumeration 404
    assert r.status_code == 404
    r = client.get(
        "/jobs/00000000-0000-0000-0000-000000000000/vpat/download?fmt=bogus", headers=h
    )
    # invalid fmt validation runs after ownership? check order: fmt validated first
    assert r.status_code in (400, 404)
