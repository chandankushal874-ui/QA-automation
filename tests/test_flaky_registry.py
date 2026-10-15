"""Tests for the cross-crawl flaky registry (quarantine).

Covers: flip-rate math, verdict thresholds, ring-buffer bounding,
cross-run promotion observed→suspected→quarantined, sticky human
confirmation, reset, absence-of-seen recording, tenant isolation,
API auth, and gate-exempt plumbing in the report.
Run: pytest tests/test_flaky_registry.py -q
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from src.api.app import SessionLocal, app, engine
from src.api.flaky_registry import (
    HISTORY_WINDOW,
    QUARANTINE_FLIP_RATE,
    SUSPECTED_FLIP_RATE,
    _flaky_severity,
    compute_flip_rate,
    record_run_outcomes,
)
from src.models.database import Base, FlakyIssue, PlatformUser

Base.metadata.create_all(bind=engine)
client = TestClient(app)


# ── Pure functions ───────────────────────────────────────────────────────────


def test_flip_rate_alternating_is_max():
    rate, transitions = compute_flip_rate([True, False, True, False, True])
    assert rate == 1.0 and transitions == 4


def test_flip_rate_stable_is_zero():
    rate, _ = compute_flip_rate([True, True, True, True])
    assert rate == 0.0


def test_flip_rate_short_history():
    assert compute_flip_rate([]) == (0.0, 0)
    assert compute_flip_rate([True]) == (0.0, 0)


def test_flip_rate_bounded_window():
    # More than HISTORY_WINDOW entries: only the tail counts
    long_hist = [True] * 30 + [True, False]
    rate, transitions = compute_flip_rate(long_hist)
    assert transitions == HISTORY_WINDOW - 1  # ring-bounded
    assert rate > 0.0


def test_verdict_thresholds():
    assert _flaky_severity(1.0, samples=2) == "observed"           # below MIN_SAMPLES
    assert _flaky_severity(0.2, samples=10) == "observed"           # stable-ish
    assert _flaky_severity(SUSPECTED_FLIP_RATE, samples=10) == "suspected"
    assert _flaky_severity(QUARANTINE_FLIP_RATE, samples=10) == "quarantined"
    assert _flaky_severity(1.0, samples=10) == "quarantined"


# ── Registry persistence across simulated runs ──────────────────────────────


@dataclass
class FakeIssue:
    fingerprint: str
    title: str = "JS_ERROR: TypeError"
    category: str = "js_error"
    severity: str = "blocking"
    first_seen_url: str = "http://x/y"


@dataclass
class FakeJob:
    url: str = "http://x"


@pytest.fixture()
def owner():
    db = SessionLocal()
    uname = f"flaky_{uuid.uuid4().hex[:8]}"
    from src.api.auth import hash_password

    u = PlatformUser(
        id=uuid.uuid4(), username=uname, email=f"{uname}@l",
        password_hash=hash_password("pw"), role="user", is_active=True,
    )
    db.add(u)
    db.commit()
    yield u.id
    # cleanup: registry rows are owner-cascaded on user delete
    db.query(FlakyIssue).filter(FlakyIssue.owner_id == u.id).delete()
    db.query(PlatformUser).filter(PlatformUser.id == u.id).delete()
    db.commit()
    db.close()


def _run(db_owner, issues):
    """One simulated crawl: which fingerprints were seen."""
    db = SessionLocal()
    try:
        job = FakeJob()
        return record_run_outcomes(db, db_owner, job, issues)
    finally:
        db.close()


def test_promotion_across_runs(owner):
    fp = f"fp-{uuid.uuid4().hex[:8]}"
    verdicts = None

    # Run 1: seen
    verdicts = _run(owner, [FakeIssue(fingerprint=fp)])
    assert verdicts[fp] == "observed"

    # Runs 2-5: alternating seen/absent → flip rate 1.0 → quarantined
    _run(owner, [])                        # not seen
    _run(owner, [FakeIssue(fingerprint=fp)])  # seen
    _run(owner, [])                        # not seen
    verdicts = _run(owner, [FakeIssue(fingerprint=fp)])  # seen (5 samples)
    assert verdicts[fp] == "quarantined"

    # Status is in the DB, tenant-scoped
    db = SessionLocal()
    try:
        row = db.query(FlakyIssue).filter(
            FlakyIssue.owner_id == owner, FlakyIssue.fingerprint == fp
        ).first()
        assert row.status == "quarantined"
        assert row.samples == 5
        assert row.flip_rate == 1.0
    finally:
        db.close()


def test_stable_issue_never_quarantined(owner):
    fp = f"fp-{uuid.uuid4().hex[:8]}"
    for _ in range(5):
        _run(owner, [FakeIssue(fingerprint=fp)])
    db = SessionLocal()
    try:
        row = db.query(FlakyIssue).filter(
            FlakyIssue.owner_id == owner, FlakyIssue.fingerprint == fp
        ).first()
        assert row.status == "observed"
        assert row.flip_rate == 0.0
    finally:
        db.close()


def test_history_bounded(owner):
    fp = f"fp-{uuid.uuid4().hex[:8]}"
    for _ in range(HISTORY_WINDOW + 10):
        _run(owner, [FakeIssue(fingerprint=fp)])
    db = SessionLocal()
    try:
        row = db.query(FlakyIssue).filter(
            FlakyIssue.owner_id == owner, FlakyIssue.fingerprint == fp
        ).first()
        assert len(row.history) <= HISTORY_WINDOW + 1
    finally:
        db.close()


def test_tenant_isolation(owner):
    # Two owners, same fingerprint: independent verdicts
    db = SessionLocal()
    from src.api.auth import hash_password

    other = PlatformUser(
        id=uuid.uuid4(), username=f"other_{uuid.uuid4().hex[:8]}",
        email="o@l", password_hash=hash_password("pw"),
        role="user", is_active=True,
    )
    db.add(other)
    db.commit()
    other_id = other.id
    db.close()

    fp = f"shared-{uuid.uuid4().hex[:8]}"
    try:
        # owner A: flaky pattern → quarantined
        _run(owner, [FakeIssue(fingerprint=fp)])
        _run(owner, [])
        _run(owner, [FakeIssue(fingerprint=fp)])
        _run(owner, [])
        _run(owner, [FakeIssue(fingerprint=fp)])
        # owner B: stable pattern → observed
        for _ in range(5):
            _run(other_id, [FakeIssue(fingerprint=fp)])

        db = SessionLocal()
        a = db.query(FlakyIssue).filter_by(owner_id=owner, fingerprint=fp).first()
        b = db.query(FlakyIssue).filter_by(owner_id=other_id, fingerprint=fp).first()
        assert a.status == "quarantined" and b.status == "observed"
        db.close()
    finally:
        db = SessionLocal()
        db.query(FlakyIssue).filter(FlakyIssue.owner_id.in_([owner, other_id])).delete()
        db.query(PlatformUser).filter(PlatformUser.id.in_([other_id])).delete()
        db.commit()
        db.close()


# ── API surface ──────────────────────────────────────────────────────────────


def _login_and_seed_user():
    uname = f"flakyapi_{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    from src.api.auth import hash_password

    u = PlatformUser(
        id=uuid.uuid4(), username=uname, email=f"{uname}@l",
        password_hash=hash_password("pw"), role="user", is_active=True,
    )
    db.add(u)
    db.commit()
    uid = u.id
    db.close()
    r = client.post("/api/auth/login", json={"username": uname, "password": "pw"})
    assert r.status_code == 200, r.text
    return uid, {"Authorization": f"Bearer {r.json()['token']}"}


def test_registry_api_requires_auth():
    assert client.get("/api/me/flaky-registry").status_code == 401


def test_registry_api_confirm_and_reset():
    uid, h = _login_and_seed_user()
    fp = f"api-{uuid.uuid4().hex[:8]}"
    _run(uid, [FakeIssue(fingerprint=fp)])

    # list contains it
    r = client.get("/api/me/flaky-registry", headers=h)
    assert r.status_code == 200
    assert any(row["fingerprint"] == fp for row in r.json()["registry"])

    # filter by status
    r = client.get("/api/me/flaky-registry?status=quarantined", headers=h)
    assert r.status_code == 200
    assert all(row["status"] == "quarantined" for row in r.json()["registry"])

    # bad status rejected
    r = client.get("/api/me/flaky-registry?status=nonsense", headers=h)
    assert r.status_code == 400

    # confirm → sticky (future runs don't demote)
    r = client.post(f"/api/me/flaky-registry/{fp}/confirm", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    for _ in range(6):
        _run(uid, [FakeIssue(fingerprint=fp)])  # stable runs can't demote
    db = SessionLocal()
    try:
        row = db.query(FlakyIssue).filter_by(owner_id=uid, fingerprint=fp).first()
        assert row.status == "confirmed"
    finally:
        db.close()

    # reset → observed, history cleared
    r = client.post(f"/api/me/flaky-registry/{fp}/reset", headers=h)
    assert r.status_code == 200
    db = SessionLocal()
    try:
        row = db.query(FlakyIssue).filter_by(owner_id=uid, fingerprint=fp).first()
        assert row.status == "observed" and row.samples == 0
    finally:
        db.close()

    # delete
    r = client.delete(f"/api/me/flaky-registry/{fp}", headers=h)
    assert r.status_code == 200
    r = client.post(f"/api/me/flaky-registry/{fp}/confirm", headers=h)
    assert r.status_code == 404

    # cleanup user
    db = SessionLocal()
    db.query(FlakyIssue).filter_by(owner_id=uid).delete()
    db.query(PlatformUser).filter_by(id=uid).delete()
    db.commit()
    db.close()


def test_registry_api_tenant_isolation():
    uid_a, _ = _login_and_seed_user()
    uid_b, hb = _login_and_seed_user()
    fp = f"iso-{uuid.uuid4().hex[:8]}"
    _run(uid_a, [FakeIssue(fingerprint=fp)])

    # user B sees nothing of user A's registry
    r = client.get("/api/me/flaky-registry", headers=hb)
    assert r.status_code == 200
    assert all(row["fingerprint"] != fp for row in r.json()["registry"])
    r = client.post(f"/api/me/flaky-registry/{fp}/confirm", headers=hb)
    assert r.status_code == 404

    for uid in (uid_a, uid_b):
        db = SessionLocal()
        db.query(FlakyIssue).filter_by(owner_id=uid).delete()
        db.query(PlatformUser).filter_by(id=uid).delete()
        db.commit()
        db.close()


def test_report_carries_quarantine_metadata(owner):
    # Gate-exempt plumbing: build a job with an issue whose fingerprint is
    # quarantined, then read /jobs/{id}/report as the owner.
    from src.models.database import Issue, IssueCategory, IssueSeverity, Job, JobStatus

    fp = f"rep-{uuid.uuid4().hex[:8]}"
    # quarantine it: 5 alternating runs
    _run(owner, [FakeIssue(fingerprint=fp)])
    _run(owner, [])
    _run(owner, [FakeIssue(fingerprint=fp)])
    _run(owner, [])
    _run(owner, [FakeIssue(fingerprint=fp)])

    db = SessionLocal()
    try:
        job = Job(
            id=uuid.uuid4(), owner_id=owner, url="http://x",
            status=JobStatus.COMPLETED,
            browser_matrix={"chromium": True, "firefox": False, "webkit": False},
        )
        db.add(job)
        db.flush()
        db.add(Issue(
            job_id=job.id, fingerprint=fp, title="JS_ERROR: flaky thing",
            severity=IssueSeverity.BLOCKING, category=IssueCategory.JS_ERROR,
            first_seen_url="http://x/y", error_message="boom",
        ))
        db.commit()
        job_id = str(job.id)
    finally:
        db.close()

    # login as owner
    db = SessionLocal()
    u = db.query(PlatformUser).filter_by(id=owner).first()
    uname = u.username
    db.close()
    r = client.post("/api/auth/login", json={"username": uname, "password": "pw"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}

    r = client.get(f"/jobs/{job_id}/report", headers=h)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["quarantined_issues"] == 1
    match = [i for i in rep["issues"] if i["fingerprint"] == fp]
    assert match and match[0]["gate_exempt"] is True
    assert match[0]["flaky_status"] == "quarantined"

    # cleanup job
    db = SessionLocal()
    db.query(Issue).filter_by(job_id=job.id).delete()
    db.query(Job).filter_by(id=job.id).delete()
    db.commit()
    db.close()
