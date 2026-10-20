"""Tests for cross-job analytics (trends, breakdowns, targets, flaky).

Seeds jobs + issues across days and asserts the aggregation math, owner
isolation, and day-bucketing (zero-filled series).

Run: pytest tests/test_analytics.py -q
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from src.api.app import app
from src.models.database import (
    Base,
    FlakyIssue,
    Issue,
    IssueCategory,
    IssueSeverity,
    Job,
    JobStatus,
    PlatformUser,
)

Base.metadata.create_all(bind=__import__("src.api.app", fromlist=["engine"]).engine)

client = TestClient(app)


def _ensure_user(username: str, password: str) -> str:
    from src.api.app import SessionLocal
    from src.api.auth import hash_password

    db = SessionLocal()
    try:
        u = db.query(PlatformUser).filter_by(username=username).first()
        if not u:
            u = PlatformUser(
                id=uuid.uuid4(), username=username, email=f"{username}@localhost",
                password_hash=hash_password(password), role="user", is_active=True,
            )
            db.add(u)
            db.commit()
        return str(u.id)
    finally:
        db.close()


def _login(username: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _seed_job(owner_id: str, url: str, status: str, created: datetime, pages: int = 10) -> str:
    from src.api.app import SessionLocal

    db = SessionLocal()
    try:
        j = Job(
            id=uuid.uuid4(), owner_id=uuid.UUID(owner_id), url=url,
            status=JobStatus(status), browser_matrix={"chromium": True, "firefox": False, "webkit": False},
            pages_visited=pages, created_at=created,
        )
        db.add(j)
        db.commit()
        return str(j.id)
    finally:
        db.close()


def _seed_issue(job_id: str, fingerprint: str, severity: str, category: str,
                created: datetime, title: str = "test issue") -> None:
    from src.api.app import SessionLocal

    db = SessionLocal()
    try:
        db.add(Issue(
            job_id=uuid.UUID(job_id), fingerprint=fingerprint, title=title,
            severity=IssueSeverity(severity), category=IssueCategory(category),
            first_seen_url="http://x", created_at=created,
        ))
        db.commit()
    finally:
        db.close()


def _cleanup(prefix: str) -> None:
    from src.api.app import SessionLocal

    db = SessionLocal()
    try:
        users = db.query(PlatformUser).filter(PlatformUser.username.like(f"{prefix}%")).all()
        for u in users:
            jobs = db.query(Job).filter_by(owner_id=u.id).all()
            for j in jobs:
                db.query(Issue).filter_by(job_id=j.id).delete()
            db.query(Job).filter_by(owner_id=u.id).delete()
            db.query(FlakyIssue).filter_by(owner_id=u.id).delete()
            db.query(PlatformUser).filter_by(id=u.id).delete()
        db.commit()
    finally:
        db.close()


_uid_a = None
_uid_b = None


def _setup() -> None:
    global _uid_a, _uid_b
    if _uid_a is None:
        _cleanup("anx_")
        _uid_a = _ensure_user("anx_a", "pw")
        _uid_b = _ensure_user("anx_b", "pw")
        now = datetime.now(UTC)
        yesterday = now - timedelta(days=1)

        # user A: 2 completed jobs today (url X), 1 failed yesterday (url Y)
        j1 = _seed_job(_uid_a, "http://x.example.com", "completed", now, pages=100)
        j2 = _seed_job(_uid_a, "http://x.example.com", "completed", now, pages=50)
        j3 = _seed_job(_uid_a, "http://y.example.com", "failed", yesterday, pages=10)
        # issues: today 1 blocking + 1 advisory on j1; yesterday 2 functional on j3
        _seed_issue(j1, "fp-block", "blocking", "js_error", now)
        _seed_issue(j1, "fp-adv", "advisory", "security", now)
        _seed_issue(j3, "fp-func-1", "functional", "network_failure", yesterday)
        _seed_issue(j3, "fp-func-2", "functional", "network_failure", yesterday)
        # recurring fingerprint (same fp across two jobs)
        _seed_issue(j2, "fp-block", "blocking", "js_error", now)

        # user B: 1 job (must never appear in A's analytics)
        jb = _seed_job(_uid_b, "http://b.example.com", "completed", now, pages=5)
        _seed_issue(jb, "fp-b", "blocking", "js_error", now)


def test_trends_series_math_and_isolation() -> None:
    _setup()
    tok = _login("anx_a", "pw")
    r = client.get("/api/analytics/trends?days=7", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["days"] == 7
    assert len(body["series"]) == 7

    today = datetime.now(UTC).date().isoformat()
    today_row = next(s for s in body["series"] if s["date"] == today)
    assert today_row["crawls"] == 2
    assert today_row["completed"] == 2
    assert today_row["issues"] == 3  # fp-block(j1) + fp-adv(j1) + fp-block(j2)
    assert today_row["blocking"] == 2
    assert today_row["advisory"] == 1
    assert today_row["pages_visited"] == 150

    totals = body["totals"]
    assert totals["crawls"] == 3
    assert totals["issues"] == 5
    assert totals["pass_rate"] == round(2 / 3, 4)

    # user B's data never leaks (3 issues total for A, not 4)
    tok_b = _login("anx_b", "pw")
    r = client.get("/api/analytics/trends?days=7", headers={"Authorization": f"Bearer {tok_b}"})
    b_today = next(s for s in r.json()["series"] if s["date"] == today)
    assert b_today["crawls"] == 1
    assert b_today["issues"] == 1


def test_trends_days_validation() -> None:
    _setup()
    tok = _login("anx_a", "pw")
    # FastAPI Query(ge/le) rejects before the handler runs -> 422
    assert client.get("/api/analytics/trends?days=0", headers={"Authorization": f"Bearer {tok}"}).status_code == 422
    assert client.get("/api/analytics/trends?days=366", headers={"Authorization": f"Bearer {tok}"}).status_code == 422
    # in-range works
    assert client.get("/api/analytics/trends?days=365", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    assert client.get("/api/analytics/trends?days=1", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_issues_breakdowns_and_top_recurring() -> None:
    _setup()
    tok = _login("anx_a", "pw")
    r = client.get("/api/analytics/issues?days=7", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["by_severity"] == {"blocking": 2, "advisory": 1, "functional": 2}
    assert body["by_category"]["js_error"] == 2
    assert body["by_category"]["network_failure"] == 2
    top = body["top_recurring"]
    assert top and top[0]["fingerprint"] == "fp-block"
    assert top[0]["occurrences"] == 2  # recurring across two jobs


def test_targets_rollup() -> None:
    _setup()
    tok = _login("anx_a", "pw")
    r = client.get("/api/analytics/targets?days=7", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    urls = {t["url"]: t for t in body["targets"]}
    assert set(urls) == {"http://x.example.com", "http://y.example.com"}
    x = urls["http://x.example.com"]
    assert x["crawls"] == 2
    assert x["completed"] == 2
    assert x["issues"] == 3
    assert x["avg_issues_per_crawl"] == 1.5
    y = urls["http://y.example.com"]
    assert y["crawls"] == 1
    assert y["issues"] == 2
    assert y["last_crawl_status"] == "failed"


def test_flaky_stats() -> None:
    global _uid_a, _uid_b
    _setup()
    # seed flaky rows for user A
    from src.api.app import SessionLocal

    db = SessionLocal()
    try:
        uid = uuid.UUID(_uid_a)
        db.add(FlakyIssue(owner_id=uid, fingerprint="fl-1", title="t1", status="quarantined",
                          flip_rate=0.8, samples=6, history=[True, False] * 3))
        db.add(FlakyIssue(owner_id=uid, fingerprint="fl-2", title="t2", status="suspected",
                          flip_rate=0.4, samples=4, history=[True, False, True, False]))
        db.commit()
    finally:
        db.close()

    tok = _login("anx_a", "pw")
    r = client.get("/api/analytics/flaky?days=7", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["registry_size"] == 2
    assert body["totals"]["quarantined"] == 1
    assert body["totals"]["suspected"] == 1
    assert body["top_flaky"][0]["fingerprint"] == "fl-1"
    assert len(body["series"]) == 7

    _cleanup("anx_")
    _uid_a = _uid_b = None


def test_analytics_requires_auth() -> None:
    assert client.get("/api/analytics/trends").status_code == 401
